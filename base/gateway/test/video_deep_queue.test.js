'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const { DelayController } = require('../src/video/congestion')
const { LinkFreshnessGuard } = require('../src/video/freshness')
const reports = []
require('node:test').after(() => {
  if (process.env.MR2_WEAK_QUEUE_REPORT) require('node:fs').writeFileSync(process.env.MR2_WEAK_QUEUE_REPORT, JSON.stringify(reports,null,2))
})
function record(name, result) {
  const firstWeak = result.rendered.find(p=>p.at>=30000&&p.at<60000)
  const firstRecovered = result.rendered.find(p=>p.at>=60000)
  const ages=result.delivered.map(p=>p.age)
  reports.push({name,admitted_packets:result.rendered.length,admitted_max_age_ms:result.maxAge,
    received_max_age_ms:ages.length?Math.max(...ages):null,final_budget_kbps:result.final.budget,
    first_fresh_in_weak_phase_s:firstWeak?(firstWeak.at-30000)/1000:null,
    first_fresh_after_recovery_s:firstRecovered?(firstRecovered.at-60000)/1000:null})
}

// A byte FIFO serviced at the CURRENT link capacity. Unlike the earlier test,
// it may retain a full MiB and create seconds of delay; capacity is not fixed
// forever when each packet is enqueued. XBee feedback has its own 40 ms path.
function simulate({ capacity, seconds = 90, guarded = true, loss = 0, jitter = 0, queueBytes = 1048576 }) {
  let now = 0, rate = 0, nextMedia = 0, sequence = 0, queuedBytes = 0, seed = 7, ssrc = 1
  const c = new DelayController({ initialKbps: 9000, maxKbps: 9000, now: () => now })
  const g = new LinkFreshnessGuard({ now: () => now, nextProbeId: () => c.nextProbeId() })
  const fifo = [], arrivals = [], commands = [], clusters = [], seen = new Set(), samples = [], delivered = [], rendered = []
  const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296 }
  function send(bytes, probe = null) {
    if (queuedBytes + bytes > queueBytes) return
    fifo.push({ bytes, left: bytes, sent: now, probe, sequence: probe ? probe.sequence++ : sequence++, ssrc })
    queuedBytes += bytes
  }
  for (now = 0; now < seconds * 1000; now += 2) {
    while (commands[0]?.at <= now) {
      const command = commands.shift()
      if (!rate && command.rate) {ssrc++; sequence = 0; nextMedia = now}
      rate = command.rate
      if (command.probe) clusters.push({ ...command.probe, next: now, end: now + command.probe.duration, sequence: 0 })
    }
    if (rate > 0 && now >= nextMedia) {
      const fps = rate * .85 >= 300 ? 30 : rate * .85 >= 180 ? 15 : rate * .85 >= 100 ? 10 : rate * .85 >= 60 ? 5 : 3
      const bytes = Math.min(1200, Math.max(80, Math.floor(rate * .9 * 1000 / 8 / fps)))
      while (now >= nextMedia) { send(bytes); nextMedia += bytes * 8 / (rate * .9) }
    }
    for (const probe of clusters) while (now >= probe.next && probe.next < probe.end) {
      send(probe.bytes, probe); probe.next += probe.bytes * 8 / probe.rate
    }
    let service = capacity(now / 1000) * 2 / 8
    while (service > 0 && fifo.length) {
      const p = fifo[0], amount = Math.min(service, p.left)
      service -= amount; p.left -= amount; queuedBytes -= amount
      if (p.left > .00001) break
      fifo.shift()
      if (random() >= loss) arrivals.push({ ...p, due: now + 10 + jitter * random() })
    }
    arrivals.sort((a,b) => a.due - b.due)
    while (arrivals[0]?.due <= now) {
      const p = arrivals.shift(), packet = {
        send_us: (p.sent + 1000000 + p.sent * .0002) * 1000,
        arrival_us: p.due * 1000, received_at_ms: now,
        ssrc: p.probe ? 0x4d523250 : p.ssrc, sequence: p.sequence, bytes: p.bytes,
        rtp_timestamp: p.probe?.id || 1,
      }
      c.observe(p.probe ? 'probe' : 'camera', packet)
      if (guarded) g.observe(packet)
      if (!p.probe) {
        delivered.push({ at: now, age: p.due - p.sent })
        const policy = g.policy(), lower = policy.lower_bound_us - Math.max(0, packet.arrival_us-policy.anchor_at_us)*.00025
        const bound = (packet.arrival_us - packet.send_us - lower) / 1000
        if (!guarded || (!policy.paused && packet.arrival_us <= policy.valid_until_us && bound >= 0 && bound <= 500))
          rendered.push({ at: now, age: p.due - p.sent })
      }
    }
    if (now % 200 === 0) {
      if (now===20000 && seconds>=90) {c.budget=8000;c.capacity=null;c.lastDecrease=-Infinity}
      let m = c.update(!guarded || g.mode === 'ready')
      if (guarded) m = g.update(m, true, c)
      samples.push({ at: now/1000, budget: m.budget_kbps, queue_bytes: queuedBytes, mode: guarded ? g.mode : 'legacy' })
      let probe = null
      if (m.probe_id && !seen.has(m.probe_id)) {
        seen.add(m.probe_id)
        const padding = m.budget_kbps === 0 ? Math.min(8, m.probe_kbps) : m.budget_kbps < 200 ?
          Math.min(m.budget_kbps*2,Math.ceil(m.probe_kbps*.9)) :
          Math.max(1, Math.min(m.budget_kbps*2, m.probe_kbps-m.receive_kbps))
        const bytes = Math.max(64, Math.min(1148, Math.floor(padding*m.probe_duration_ms/80)))
        probe = { id:m.probe_id, rate:padding, bytes, duration:m.probe_duration_ms }
      }
      commands.push({ at:now+40, rate:m.budget_kbps, probe })
    }
  }
  const final = samples.at(-1), maxAge = rendered.length ? Math.max(...rendered.map(p=>p.age)) : null
  return { samples, delivered, rendered, final, maxAge }
}

for (const low of [32, 100, 300]) for (const loss of [0, .02, .05]) {
  test(`deep FIFO 8000 -> ${low} -> 8000 kbps, loss ${loss}, never replays multi-second media`, () => {
    const capacity = s => s < 30 || s >= 60 ? 8000 : low
    const r = simulate({ capacity, loss, jitter:5 })
    record(`8000-${low}-8000-loss-${loss}`,r)
    assert(r.rendered.length > 100, 'no useful media was delivered')
    assert(r.maxAge <= 500, `old media age=${r.maxAge}`)
    assert(r.samples.some(s=>s.at>=30 && s.mode==='draining'), 'collapse was not detected')
    assert(r.rendered.some(p=>p.at>70000), 'media failed to recover after the link returned')
    assert(r.final.budget > 1000, JSON.stringify(r.final))
  })
}

for (const rate of [32, 100, 1000]) {
  test(`cold startup on ${rate} kbps needs no pre-existing clean-delay baseline`, () => {
    const r = simulate({ capacity:()=>rate, seconds:45, loss:.01, jitter:5 })
    record(`cold-${rate}`,r)
    assert(r.rendered.length>=10, JSON.stringify(r.final))
    assert(r.maxAge<=500, `age=${r.maxAge}`)
    assert(r.final.budget >= rate * .4, JSON.stringify(r.final))
  })
}

test('multi-second FIFO reproduces the old delayed-playback problem in the control run', () => {
  const r = simulate({ capacity:s=>s<10?8000:100, seconds:25, guarded:false })
  record('control-old-playback',r)
  assert(r.maxAge > 2000, `control did not create a realistic queue: ${r.maxAge}`)
})

module.exports = { simulate }
