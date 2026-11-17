'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const { DelayController } = require('../src/video/congestion')
const { LinkFreshnessGuard } = require('../src/video/freshness')
const { probeToken } = require('../src/video/control_protocol')

function fixture() {
  let now = 0
  const controller = new DelayController({ initialKbps: 9000, maxKbps: 9000, now: () => now })
  const guard = new LinkFreshnessGuard({ now: () => now, nextProbeId: () => controller.nextProbeId() })
  const tick = (active = true) => guard.update(controller.update(active && guard.mode === 'ready'), active, controller)
  const probe = (id, at, delay = 50, epoch = 1000000000) => {
    now = at + delay
    guard.observe({ ssrc: 0x4d523250, rtp_timestamp: id,
      send_us: (at + 20) * 1000 + epoch, arrival_us: now * 1000 })
  }
  return { controller, guard, tick, probe, setTime: t => { now = t } }
}

test('probe tokens distinguish gateway sessions and match the native sender', () => {
  assert.equal(probeToken(100, 7), 1759324492)
  assert.equal(probeToken(123, 42), 774359804)
  assert.notEqual(probeToken(100, 7), probeToken(101, 7))
  let now = 0
  const c = new DelayController({ now: () => now })
  const g = new LinkFreshnessGuard({ now: () => now, nextProbeId: () => c.nextProbeId(), token: id => probeToken(100, id) })
  const m = g.update(c.update(false), true, c)
  now = 50
  g.observe({ ssrc: 0x4d523250, rtp_timestamp: probeToken(99, m.probe_id), send_us: 1000020000, arrival_us:50000 })
  assert.equal(g.recovered, false)
})

test('startup proves the video path with a tiny XBee-requested probe before admitting media', () => {
  const f = fixture(), first = f.tick()
  assert.equal(first.budget_kbps, 0)
  assert.equal(first.probe_kbps, 8)
  assert.equal(f.guard.policy().paused, true)
  f.probe(first.probe_id, 0)
  assert.equal(f.tick().budget_kbps, 32)
  assert.equal(f.guard.policy().paused, false)
  assert.equal(f.guard.policy().lower_bound_us, -1000020012.5)
})

test('an old probe reply cannot authorize replay of an already congested path', () => {
  const f = fixture(), first = f.tick()
  f.probe(first.probe_id, 0, 2500)
  assert.equal(f.guard.policy().paused, true)
  assert.equal(f.tick().budget_kbps, 0)
})

test('persistent multi-second media age pauses all media and keeps only small path probes', () => {
  const f = fixture(), first = f.tick()
  f.probe(first.probe_id, 0); f.tick()
  f.controller.budget = 9000
  for (let i = 0; i < 4; i++) {
    f.setTime(400 + i * 100)
    for (let j = 0; j < 5; j++) f.guard.observe({ ssrc: 1, sequence: i * 5 + j,
      send_us: 998000000 + i * 100000, arrival_us: (400 + i * 100) * 1000, bytes: 1000 })
    f.tick()
  }
  assert.equal(f.guard.mode, 'draining')
  const m = f.tick()
  assert.equal(m.budget_kbps, 0)
  assert(m.probe_id === 0 || m.probe_kbps === 8)
  assert.equal(f.guard.policy().paused, true)
  f.probe(first.probe_id, 0, 50) // Even a bounded reply from before the drain is obsolete.
  assert.equal(f.guard.recovered, false)
})

test('inactive cameras never trigger probes or media, and a new active session requires new proof', () => {
  const f = fixture()
  assert.equal(f.tick(false).probe_id, 0)
  assert.equal(f.tick(false).budget_kbps, 0)
  const m = f.tick(true)
  assert(m.probe_id)
  f.probe(m.probe_id, 0); f.tick(true)
  assert.equal(f.guard.mode, 'ready')
  f.tick(false)
  assert.equal(f.guard.mode, 'bootstrap')
})

test('clock-offset bounds cover negative epochs and drift without wall-clock timestamps', () => {
  const f = fixture(), first = f.tick()
  f.probe(first.probe_id, 0, 50, -1000000000); f.tick()
  assert.equal(f.guard.policy().lower_bound_us, 999979987.5)
  assert(f.guard.policy().valid_until_us > 14000000)
})

test('a quality upgrade from 3 FPS is not mistaken for a silent failed radio', () => {
  const f=fixture(), first=f.tick()
  f.probe(first.probe_id,0);f.tick();f.controller.budget=1000
  let nextFrame=50
  for(let at=50;at<=2050;at+=50) {
    f.setTime(at)
    if(at>=nextFrame) {
      f.guard.observe({ssrc:1,send_us:1000000000+(at-10)*1000,arrival_us:at*1000,bytes:100})
      nextFrame+=1000/3
    }
    if(at%200===50) {
      const m=f.guard.update(f.controller.update(true),true,f.controller,1000/3)
      assert(m.budget_kbps>=1000,JSON.stringify(m))
      assert.equal(f.guard.mode,'ready')
    }
  }
})

test('probe capacity uses media in the probe interval, not an unrelated one-second average', () => {
  let now=0
  const c=new DelayController({initialKbps:1000,maxKbps:9000,now:()=>now})
  for(let i=0;i<30;i++)c.observe('media',{ssrc:1,sequence:i,send_us:i*1000,arrival_us:i*1000+10000,bytes:4000})
  c.probe={id:7,started:0,target:1800,baselineReceive:1500,duration:100,packets:[]}
  for(let i=0;i<5;i++)c.observe('probe',{ssrc:0x4d523250,sequence:i,rtp_timestamp:7,send_us:100000+i*25000,arrival_us:110000+i*25000,bytes:1000})
  now=800;c.update(true)
  assert.equal(c.lastProbeResult.achieved_kbps,320)
  assert.equal(c.lastProbeResult.success,false)
  assert(c.budget<=1000)
})
