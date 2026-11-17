'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const { DelayController, PacketObservations } = require('../src/video/congestion')
const { createVideoControl } = require('../src/video/control')
const { VIDEO_SET, VIDEO_FEEDBACK, VIDEO_KEYFRAME, decodeState } = require('../src/video/control_protocol')
const { probeToken } = require('../src/video/control_protocol')

function state({ request = 0, session = 0, flags = 3, result = 0, boot = 1, port = 5010, status = 1, bitrate = 0 } = {}) {
  const b = Buffer.alloc(26)
  b.writeUInt32LE(boot, 0); b.writeUInt32LE(session, 4); b.writeUInt32LE(request, 8)
  b.writeUInt16LE(port, 12); b[14] = status; b[15] = flags; b[25] = result
  b.writeUInt32LE(bitrate, 16)
  return b
}
function fixture() {
  let now = 0, seq = 0
  const frames = [], messages = []
  const control = createVideoControl({ streams: [{ stream_id: 'front', udp_port: 5010, encoder: { bitrate_kbps: 1800 } }],
    session: 123, now: () => now, nextSeq: () => seq++ & 255,
    writeFrame: (f) => { frames.push(f); return true }, broadcast: (m) => messages.push(m) })
  return { control, frames, messages, advance: (ms) => { now += ms } }
}
test('keyframe recovery travels over XBee with a bounded per-camera request rate', () => {
  const f = fixture()
  assert.equal(f.control.requestKeyframe('missing'), false)
  assert.equal(f.control.requestKeyframe('front'), true)
  assert.equal(f.frames[0][1], VIDEO_KEYFRAME)
  assert.equal(f.frames[0].readUInt32LE(4), 123)
  assert.equal(f.frames[0].readUInt16LE(12), 5010)
  for (let i = 0; i < 20; i++) assert.equal(f.control.requestKeyframe('front'), false)
  f.advance(500)
  assert.equal(f.control.requestKeyframe('front'), true)
  assert.equal(f.frames.length, 2)
})
test('commands retry idempotently, acknowledge rover state, and send compact feedback over XBee', () => {
  const f = fixture()
  assert(f.control.command({ stream_id: 'front', enabled: true, priority: 'high' }))
  assert.equal(f.frames[0][1], VIDEO_SET)
  f.advance(600); f.control.tick()
  assert.deepEqual(f.frames[0].subarray(4, 16), f.frames[1].subarray(4, 16))
  f.control.handleState(state({ session: 123, request: 1 }))
  assert.equal(f.control.get('front').state, 'ON')
  assert(f.messages.some((m) => m.type === 'video_command_result' && m.success))
  f.control.tick()
  assert.equal(f.frames.at(-1)[1], VIDEO_FEEDBACK)
  assert.equal(f.frames.at(-1).length, 28)
  const count = f.frames.filter((b) => b[1] === VIDEO_SET).length
  f.advance(600); f.control.tick()
  assert.equal(f.frames.filter((b) => b[1] === VIDEO_SET).length, count)
})
test('no device retains requested enable, lock rejection is reported, old state cannot acknowledge a newer command', () => {
  const f = fixture()
  const missing = state(); missing[14] = 2
  assert.deepEqual([decodeState(missing).state, decodeState(missing).enabled], ['NO DEVICE', true])
  assert.equal(decodeState(Buffer.alloc(25)), null)
  f.control.command({ stream_id: 'front', enabled: false, priority: 'low' })
  f.control.handleState(state({ session: 123, request: 0 }))
  assert.equal(f.messages.filter((m) => m.type === 'video_command_result').length, 0)
  f.control.handleState(state({ session: 123, request: 1, result: 1 }))
  assert(f.messages.some((m) => m.success === false && m.message.includes('locked')))
})
test('malformed commands are rejected and unanswered commands eventually fail', () => {
  const f = fixture()
  assert.equal(f.control.command({ stream_id: 'unknown', enabled: true, priority: 'high' }), false)
  f.control.command({ stream_id: 'front', enabled: false, priority: 'low' })
  for (let i = 0; i < 8; i++) { f.advance(600); f.control.tick() }
  assert(f.messages.some((m) => m.type === 'video_command_result' && m.success === false))
})
test('delay estimator backs off on rising queue, recovers on stable utilized link, and holds when application limited', () => {
  let now = 0
  const c = new DelayController({ initialKbps: 1000, maxKbps: 6000, now: () => now })
  let arrival = 0, sequence = 0
  for (let i = 0; i < 120; i++) {
    now = i * 20; arrival = i * 22 + 10
    c.observe('front', { send_us: i * 20000, arrival_us: arrival * 1000, bytes: 2500, sequence: sequence++, ssrc: 1 })
    if (i % 10 === 0) c.update()
  }
  const congested = c.update().budget_kbps
  assert(congested < 900, `budget=${congested}`)
  for (let i = 120; i < 400; i++) {
    now = i * 20
    // The congestion queue drains instead of preserving a permanent 238 ms
    // latency penalty while asking a low-latency controller to increase.
    const drainedArrival = i * 20 + 10 + Math.max(0, 238 - (i - 120) * 2)
    c.observe('front', { send_us: i * 20000, arrival_us: drainedArrival * 1000, bytes: 2500, sequence: sequence++, ssrc: 1 })
    if (i % 10 === 0) c.update()
  }
  assert(c.update().budget_kbps > congested)
  const limited = new DelayController({ initialKbps: 6000, now: () => now })
  for (let i = 0; i < 100; i++) {
    now += 20; limited.observe('front', { send_us: now * 1000, arrival_us: (now + 10) * 1000, bytes: 100, sequence: i, ssrc: 1 })
    limited.update()
  }
  assert.equal(limited.update().budget_kbps, 6000)
})
test('sequence wrap, duplicates and prompt reordering are not packet loss', () => {
  let now = 0
  const c = new DelayController({ now: () => now })
  for (const sequence of [65534, 0, 65535, 1, 1, 2]) {
    c.observe('front', { send_us: now * 1000, arrival_us: (now + 10) * 1000, bytes: 1200, sequence, ssrc: 1 }); now += 5
  }
  now += 150
  assert.equal(c.update().loss, 0)
})
test('observations preserve receiver timestamps across partial pipe reads', () => {
  const packets = [], parser = new PacketObservations((p) => packets.push(p))
  const b = Buffer.alloc(52)
  b.writeBigUInt64BE(100000n, 0); b.writeBigUInt64BE(90000n, 16)
  b.writeUInt16BE(1000, 42); b.writeUInt32BE(0x4d523201, 44)
  parser.push(b.subarray(0, 7)); parser.push(b.subarray(7))
  assert.equal(packets[0].arrival_us, 100000)
})

test('local observation drops do not masquerade as radio packet loss', () => {
  let now = 0
  const c = new DelayController({ now: () => now })
  for (const [sequence, observation_drops] of [[1, 0], [30, 29], [31, 29]]) {
    c.observe('front', { send_us: now * 1000, arrival_us: (now + 10) * 1000, bytes: 1200, sequence, observation_drops, ssrc: 1 }); now += 20
  }
  now += 150
  assert.equal(c.update().loss, 0)
})

test('bounded scheduling jitter does not collapse a healthy link budget', () => {
  let now = 0
  const c = new DelayController({ initialKbps: 3000, maxKbps: 6000, now: () => now })
  for (let i = 0; i < 2400; i++) {
    now = i * 5
    c.observe('front', { send_us: now * 1000, arrival_us: (now + 20 + 8 * Math.sin(i * .2)) * 1000,
      bytes: 1500, sequence: i, ssrc: 1 })
    if (i % 40 === 0) c.update()
  }
  assert(c.update().budget_kbps >= 2700)
})
test('silence recovery permits an encoder to emit a fresh keyframe after a profile restart', () => {
  let now = 0
  const c = new DelayController({ initialKbps: 1200, maxKbps: 3000, now: () => now })
  c.observe('front', { send_us: 0, arrival_us: 10000, bytes: 1200, sequence: 0, ssrc: 1 })
  const budgets = []
  for (now = 200; now <= 6000; now += 200) budgets.push(c.update().budget_kbps)
  assert(budgets.every((b) => b === 1200), 'silence must not force an encoder below a usable rate')
})

test('configured radio budget ceiling caps initial feedback', () => {
  const frames = []
  const c = createVideoControl({ streams: [{ stream_id: 'front', udp_port: 5010, encoder: { bitrate_kbps: 1800 } }],
    initialKbps: 3000, maxKbps: 1000, session: 123, nextSeq: () => 0,
    writeFrame: (f) => frames.push(f) })
  c.handleState(state()); c.tick()
  assert.equal(frames.at(-1).readUInt32LE(12), 1000)
})

test('an intentional encoder reconfiguration is not treated as a network outage', () => {
  let now = 0
  const c = new DelayController({ initialKbps: 2000, now: () => now })
  c.observe('front', { send_us: 0, arrival_us: 10000, bytes: 1200, sequence: 0, ssrc: 1 })
  c.configurationChanged()
  now = 1200
  assert.equal(c.update().budget_kbps, 2000)
})

test('browser class utilization uses observed wire traffic and rover allocations, excluding OFF and missing devices', () => {
  let now = 0
  const messages = [], frames = []
  const streams = ['high', 'low', 'off', 'missing'].map((id, i) => ({
    stream_id: id, udp_port: 5010 + 2 * i, priority: i === 0 ? 'high' : 'low', encoder: { bitrate_kbps: 1800 },
  }))
  const c = createVideoControl({ streams, now: () => now, nextSeq: () => 0,
    writeFrame: (f) => frames.push(f), broadcast: (m) => messages.push(m) })
  c.tick()
  assert.equal(c.getMetrics().classes.high.budget_kbps, null)
  c.handleState(state({ bitrate: 850 }))
  c.handleState(state({ port: 5012, flags: 2, bitrate: 170 }))
  c.handleState(state({ port: 5014, flags: 0, status: 0, bitrate: 1800 }))
  c.handleState(state({ port: 5016, flags: 2, status: 2, bitrate: 1800 }))
  for (let i = 0; i < 10; i++) {
    now = 100 + i * 100
    for (const [id, bytes] of [['high', 5000], ['low', 1250]]) {
      const packet = { send_us: now * 1000, arrival_us: now * 1000, bytes, sequence: i, ssrc: 1 }
      c.observe(id, packet)
      c.observe(id, packet) // Duplicate observations must not inflate utilization.
    }
  }
  now = 1100; c.tick()
  const m = c.getMetrics()
  // Both class rates use the same receive-time span as the GCC aggregate.
  assert.equal(m.classes.high.budget_kbps, 1000)
  assert.equal(m.classes.low.budget_kbps, 200)
  assert.equal(m.classes.high.streams, 1)
  assert.equal(m.classes.low.streams, 1)
  assert(Math.abs(m.classes.high.receive_kbps - 400000 / 900) < 1e-8)
  assert(Math.abs(m.classes.low.receive_kbps - 100000 / 900) < 1e-8)
  assert(Math.abs(m.receive_kbps - 500000 / 900) < 1e-8)
  assert(Math.abs(m.classes.high.utilization_pct - 400 / 9) < 1e-8)
  assert(Math.abs(m.classes.low.utilization_pct - 500 / 9) < 1e-8)
  assert.equal(frames.filter((f) => f[1] === VIDEO_FEEDBACK).length, 1)
  assert.equal(frames.at(-1).length, 28, 'monitoring must not expand radio feedback')
  const count = messages.filter((m) => m.type === 'video_link_metrics').length
  now += 200; c.tick()
  assert.equal(messages.filter((m) => m.type === 'video_link_metrics').length, count)
  now += 1000; c.tick()
  assert.equal(c.getMetrics().receive_kbps, 0)
  assert.equal(c.getMetrics().classes.low.utilization_pct, 0)
  // A priority change moves both its allocation and current receive history.
  c.handleState(state({ port: 5012, flags: 3, bitrate: 170 }))
  now += 1000; c.tick()
  assert.equal(c.getMetrics().classes.high.budget_kbps, 1200)
  assert.equal(c.getMetrics().classes.low.budget_kbps, 0)
  assert.equal(c.getMetrics().classes.low.utilization_pct, null)
})

test('latency telemetry reports real epoch delay and relative queue, excludes duplicates/probes, and expires', () => {
  let now = 0
  const frames = []
  const control = createVideoControl({ streams: [{ stream_id: 'front', udp_port: 5010, encoder: { bitrate_kbps: 1800 } }],
    now: () => now, nextSeq: () => 0, writeFrame: (f) => frames.push(f) })
  control.handleState(state())
  const epoch = 1_800_000_000_000_000
  for (let i = 0; i < 20; i++) {
    // Different monotonic clock origins must not leak into absolute telemetry.
    const delay = 20 + i
    const packet = { ssrc: 1, sequence: i, bytes: 1200,
      send_us: 9_000_000_000 + i * 10000, arrival_us: i * 10000 + delay * 1000,
      send_epoch_us: epoch + i * 10000, arrival_epoch_us: epoch + i * 10000 + delay * 1000 }
    control.observe('front', packet)
    control.observe('front', packet)
  }
  control.observe('front', { ssrc: 0x4d523250, sequence: 100, bytes: 1200,
    send_us: 0, arrival_us: 500000, send_epoch_us: epoch, arrival_epoch_us: epoch + 500000 })
  now = 1000; control.tick()
  const metrics = control.getMetrics()
  assert.equal(metrics.network_delay_samples, 20)
  assert.equal(metrics.network_delay_ms, 29)
  assert.equal(metrics.network_delay_p95_ms, 38)
  assert.equal(metrics.network_delay_min_ms, 20)
  assert(metrics.queue_delay_ms > 5 && metrics.queue_delay_ms < 15)
  assert.equal(frames.at(-1).length, 28, 'latency telemetry must not enlarge XBee feedback')
  now += 1200; control.tick()
  assert.equal(control.getMetrics().network_delay_samples, 0)
  assert.equal(control.getMetrics().network_delay_ms, null)
  assert.equal(control.getMetrics().network_delay_p95_ms, null)
  assert.equal(control.getMetrics().network_delay_min_ms, null)
  assert.equal(control.getMetrics().queue_delay_ms, null)
})

test('missing/invalid/backward epoch timestamps never create a false zero one-way delay', () => {
  let now = 0
  const controller = new DelayController({ now: () => now })
  const timestampPairs = [[undefined, undefined], [0, 0], [NaN, 1000], [10000, 9999]]
  for (const [sequence, [send_epoch_us, arrival_epoch_us]] of timestampPairs.entries()) {
    now += 10
    controller.observe('front', { send_us: now * 1000, arrival_us: now * 1000 + 10000,
      bytes: 1200, sequence, ssrc: 1, send_epoch_us, arrival_epoch_us })
  }
  const metrics = controller.update()
  assert.equal(metrics.network_delay_samples, 0)
  assert.equal(metrics.network_delay_ms, null)
  assert.equal(metrics.network_delay_p95_ms, null)
  assert.equal(metrics.network_delay_min_ms, null)
  assert(metrics.receive_kbps > 0, 'legacy sender traffic must still drive GCC')
})
