import assert from 'node:assert/strict'
import { VideoLatencyMinimum, videoLinkLatency } from '../src/lib/videoLinkLatency'
import type { VideoLinkMetrics } from '../src/lib/xbeeGateway'

const ready = { ready: true, reasons: [], roverErrorBoundS: .001 }
const metrics: VideoLinkMetrics = {
  type: 'video_link_metrics', updated_at_ms: 1000,
  budget_kbps: 1000, receive_kbps: 800, loss_pct: 0, trend_ms_s: 0,
  network_delay_ms: 24.56, network_delay_p95_ms: 50.12, network_delay_samples: 20,
  network_delay_min_ms: 12.34,
  queue_delay_ms: 3.45,
  classes: {
    high: { budget_kbps: 800, receive_kbps: 640, utilization_pct: 80, streams: 1 },
    low: { budget_kbps: 200, receive_kbps: 160, utilization_pct: 80, streams: 1 },
  },
}
assert.equal(videoLinkLatency(metrics, ready).median, '24.6 ms')
assert.equal(videoLinkLatency(metrics, ready).p95, '50.1 ms')
assert.equal(videoLinkLatency(metrics, ready, 12.34).minimum, '12.3 ms')
assert.equal(videoLinkLatency(metrics, ready).queue, '3.5 ms')
const blocked = videoLinkLatency(metrics, {
  ready: false, reasons: ['Rover clock status is stale'], roverErrorBoundS: null,
})
assert.equal(blocked.median, 'Clock sync')
assert.equal(blocked.p95, 'Clock sync')
assert.equal(blocked.minimum, 'Clock sync')
assert.equal(blocked.queue, '3.5 ms', 'relative queue delay does not need synchronized clocks')
assert.ok(blocked.title.endsWith('Clock sync'))
for (const value of [null, undefined, { ...metrics, receive_kbps: 0 }]) {
  const display = videoLinkLatency(value, ready)
  assert.deepEqual([display.median, display.p95, display.queue], ['—', '—', '—'])
  assert.equal(display.minimum, '—')
}
for (const value of [null, undefined, -1, NaN, Infinity]) {
  const display = videoLinkLatency({ ...metrics, network_delay_ms: value }, ready)
  assert.equal(display.median, '—', 'invalid delay must not be shown as zero latency')
  assert.equal(videoLinkLatency({ ...metrics, network_delay_min_ms: value }, ready).minimum, '—')
}
assert.equal(videoLinkLatency({ ...metrics, network_delay_ms: 0 }, ready).median, '0.0 ms')
const minimum = new VideoLatencyMinimum()
minimum.observe(metrics, false)
assert.equal(minimum.value, null, 'unsynchronized samples must not establish a minimum')
minimum.observe(metrics, true)
assert.equal(minimum.value, 12.34)
minimum.observe({ ...metrics, network_delay_min_ms: 25 }, true)
assert.equal(minimum.value, 12.34, 'the minimum must survive later slower samples')
minimum.observe({ ...metrics, network_delay_min_ms: 5 }, true)
assert.equal(minimum.value, 5)
minimum.observe({ ...metrics, network_delay_min_ms: 1 }, false)
minimum.observe({ ...metrics, network_delay_min_ms: 0, receive_kbps: 0 }, true)
assert.equal(minimum.value, 5, 'clock/data loss must preserve the previous verified minimum')
assert.equal(videoLinkLatency(null, ready, minimum.value).minimum, '5.0 ms')
assert.equal(videoLinkLatency(metrics, { ...ready, ready: false }, minimum.value).minimum, '5.0 ms')
for (const value of [undefined, null, -1, NaN, Infinity]) {
  minimum.observe({ ...metrics, network_delay_min_ms: value }, true)
  assert.equal(minimum.value, 5)
}
minimum.observe({ ...metrics, network_delay_min_ms: 0 }, true)
assert.equal(minimum.value, 0, 'valid zero must not be treated as missing')
console.log('video link latency display and clock gating test passed')
