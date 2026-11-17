'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const { DelayController } = require('../src/video/congestion')

for (const sustained of [false, true]) {
  test(`${sustained ? 'persistent' : 'isolated'} 80 ms queue tail with a zero median`, () => {
    let now = 0
    const c = new DelayController({ initialKbps: 3000, now: () => now })
    for (now = 0; now <= 600; now += 200) {
      c.lastPacket = now
      c.arrivals = Array.from({ length: 100 }, (_, i) => ({ id: 'camera', at: now - 199 + i * 2,
        bytes: 600, queue_ms: (sustained || now === 0) && i % 10 === 0 ? 80 : 0, network_delay_ms: null }))
      const m = c.update(true)
      assert.equal(m.queue_ms, 0)
      if (now === 600) assert(sustained ? m.budget_kbps < 3000 : m.budget_kbps >= 3000, JSON.stringify(m))
    }
  })
}

test('one camera changing encoder does not erase the shared link congestion evidence', () => {
  let now = 1000
  const c = new DelayController({ now: () => now })
  c.observe('front', { send_us: 1000000, arrival_us: 1010000, bytes: 1200, sequence: 7, ssrc: 1 })
  c.points = [{ x: 1, y: 1 }, { x: 20, y: 15 }]
  c.trend = 30
  c.overuseSince = 500
  const floor = [...c.transitFloors.values()][0].value
  now += 1
  c.observe('front', { send_us: 1001000, arrival_us: 1011000, bytes: 1200, sequence: 1234, ssrc: 2 })
  assert.equal(c.points.length, 2)
  assert.equal(c.trend, 30)
  assert.equal(c.overuseSince, 500)
  assert.equal([...c.transitFloors.values()][0].value, floor)
  assert.equal(c.reconfiguringUntil, -Infinity)
})

// Packet-level FIFO bottleneck with finite buffering. Both media and real
// probe clusters consume capacity; the estimator sees only delivered packets.
function simulate({ capacity, initial = 300, utilization = .9, loss = 0, jitter = 0, seconds = 45 }) {
  let now = 0, available = 0, nextVideo = 0, sequence = 0, seed = 12345, lastProbe = 0
  const controller = new DelayController({ initialKbps: initial, maxKbps: 12000, now: () => now })
  const pending = [], samples = [], delays = [], clusters = []
  const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296 }
  let metrics = { budget_kbps: initial, receive_kbps: 0 }
  function send(at, bytes, probeId = 0, probeSequence = 0) {
    const packetSequence = probeId ? probeSequence : sequence++
    const rate = capacity(at / 1000)
    if (rate <= 0) { available = at; return }
    const finish = Math.max(available, at) + bytes * 8 / rate
    if (Math.max(0, available - at) > 150) return
    available = finish
    if (random() < loss) return
    const arrival = finish + 10 + jitter * random()
    pending.push({ due: arrival, id: probeId ? 'probe' : 'camera', p: {
      send_us: at * 1000, arrival_us: arrival * 1000, received_at_ms: arrival,
      bytes, sequence: packetSequence, ssrc: probeId ? 0x4d523250 : 1,
      rtp_timestamp: probeId,
    } })
    if (!probeId) delays.push({ at, delay: arrival - at })
  }
  for (now = 0; now < seconds * 1000; now += 2) {
    const transmissions = []
    while (now >= nextVideo) {
      transmissions.push([nextVideo, 1200])
      nextVideo += 1200 * 8 / Math.max(1, metrics.budget_kbps * utilization)
    }
    for (const cluster of clusters) {
      while (now >= cluster.next && cluster.next < cluster.end) {
        transmissions.push([cluster.next, cluster.bytes, cluster.id, cluster.sequence++])
        cluster.next += cluster.bytes * 8 / cluster.rate
      }
    }
    transmissions.sort((a, b) => a[0] - b[0])
    for (const args of transmissions) send(...args)
    pending.sort((a, b) => a.due - b.due)
    while (pending[0]?.due <= now) {
      const packet = pending.shift(); controller.observe(packet.id, packet.p)
    }
    if (now % 200 === 0) {
      metrics = controller.update(true)
      samples.push({ at: now / 1000, ...metrics })
      if (metrics.probe_id && metrics.probe_id !== lastProbe) {
        lastProbe = metrics.probe_id
        const padding = metrics.budget_kbps < 200 ?
          Math.min(metrics.budget_kbps * 2, Math.ceil(metrics.probe_kbps * .9)) :
          Math.max(1, Math.min(metrics.budget_kbps * 2, metrics.probe_kbps - metrics.receive_kbps))
        const bytes = Math.max(64, Math.min(1148, Math.floor(padding * metrics.probe_duration_ms / 80)))
        clusters.push({ id: lastProbe, rate: padding, bytes, next: now + 100,
          end: now + 100 + metrics.probe_duration_ms, sequence: 0 })
      }
    }
  }
  return { samples, delays, last: samples.at(-1), clusters: clusters.length }
}

for (const capacity of [32, 50, 100, 300, 1000, 3000, 8000]) {
  for (const utilization of [.9, .35]) {
    test(`capacity ${capacity} kbps, ${utilization === .9 ? 'busy' : 'application-limited'} media discovers usable bandwidth`, () => {
      const r = simulate({ capacity: () => capacity, initial: Math.min(300, capacity), utilization })
      assert(r.last.budget_kbps >= capacity * .55, JSON.stringify(r.last))
      assert(r.last.budget_kbps <= capacity * 1.5 + 100, JSON.stringify(r.last))
      assert(r.samples.every((m) => Number.isFinite(m.budget_kbps) && m.budget_kbps >= 32 && m.budget_kbps <= 12000))
      const stable = r.delays.filter((p) => p.at > 35000).map((p) => p.delay).sort((a, b) => a - b)
      // At very low rates one full-size packet itself takes 100–300 ms;
      // allow one 200 ms feedback interval on top of serialization. The
      // encoder tests separately cover smaller low-resolution video frames.
      const latencyLimit = capacity < 150 ? 9600 / capacity + 200 : 100
      assert(!stable.length || stable[Math.floor(stable.length * .95)] < latencyLimit,
        `p95=${stable[Math.floor(stable.length * .95)]} ms, budget=${r.last.budget_kbps}`)
    })
  }
}

for (const jitter of [0, 5, 15]) {
  test(`abrupt 6000 -> 300 -> 5000 kbps, jitter ${jitter} ms, recovers without permanent starvation`, () => {
    const capacity = (s) => s < 8 ? 6000 : s < 23 ? 300 : 5000
    const r = simulate({ capacity, initial: 4000, jitter, seconds: 60 })
    const low = r.samples.filter((p) => p.at >= 18 && p.at < 23)
    assert(Math.min(...low.map((p) => p.budget_kbps)) <= 450)
    assert(r.last.budget_kbps >= 2500, JSON.stringify(r.last))
    assert(r.delays.every((p) => p.delay <= 160 + 9600 / capacity(p.at / 1000) + jitter))
  })
}

test('complete outage, link recovery, and 2% random loss do not require a process restart', () => {
  const r = simulate({ capacity: (s) => s >= 8 && s < 13 ? 0 : 3000, initial: 2000, loss: .02, seconds: 60 })
  assert(r.last.budget_kbps > 1200, JSON.stringify(r.last))
  assert(r.clusters > 2)
})

test('probe traffic never inflates camera receive statistics or creates camera packet loss', () => {
  let now = 2000
  const c = new DelayController({ initialKbps: 300, now: () => now })
  c.started = 0
  const m = c.update(true)
  assert(m.probe_id)
  for (let i = 0; i < 10; i++) {
    now += 15
    c.observe('camera', { send_us: now * 1000, arrival_us: (now + 10) * 1000, bytes: 900,
      sequence: i, ssrc: 0x4d523250, rtp_timestamp: m.probe_id })
  }
  now += 800
  const result = c.update(true)
  assert.equal(result.receive_kbps, 0)
  assert.deepEqual(result.receive_by_stream_kbps, {})
  assert.equal(result.loss, 0)
  assert(result.budget_kbps > 300)
})

test('different monotonic epochs and 200 ppm clock drift do not masquerade as a growing network queue', () => {
  let now = 0
  const c = new DelayController({ initialKbps: 3000, maxKbps: 3000, now: () => now })
  for (let i = 0; i < 60000; i++) {
    now = i * 20
    c.observe('camera', { send_us: (now + 9000000) * 1000,
      arrival_us: (now + 20 + now * .0002) * 1000,
      bytes: 1000, sequence: i & 65535, ssrc: 1, received_at_ms: now })
    if (i % 10 === 0) assert.equal(c.update().budget_kbps, 3000)
  }
  assert(c.update().queue_ms < 1)
})
