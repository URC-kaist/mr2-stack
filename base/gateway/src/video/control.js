'use strict'
const { encodeFrame } = require('../protocol/xbee')
const { VIDEO_SET, VIDEO_FEEDBACK, VIDEO_KEYFRAME, newer, probeToken, setPayload, feedbackPayload, decodeState } = require('./control_protocol')
const { DelayController } = require('./congestion')

function createVideoControl({ streams, nextSeq, writeFrame, broadcast = () => {}, initialKbps = 3000, maxKbps = 0, now = () => Number(process.hrtime.bigint()) / 1e6, session = Date.now() >>> 0, freshness = false, onReceivePolicy = () => {} }) {
  const states = new Map()
  const pending = new Map()
  let request = 0, feedback = 0, timer = null
  let keySequence = 0
  const lastKeyframe = new Map()
  function requestKeyframe(id) {
    const stream = streams.find((s) => s.stream_id === id)
    if (!stream || now() - (lastKeyframe.get(id) ?? -Infinity) < 500) return false
    const payload = Buffer.alloc(10)
    payload.writeUInt32LE(session >>> 0, 0); payload.writeUInt32LE(++keySequence >>> 0, 4); payload.writeUInt16LE(stream.udp_port, 8)
    if (writeFrame(encodeFrame(VIDEO_KEYFRAME, nextSeq(), payload), 'video') === false) return false
    lastKeyframe.set(id, now()); return true
  }
  let observations = []
  let lastMetricsAt = -Infinity, latestMetrics = null
  const configuredMax = streams.reduce((n, s) => n + (s.encoder.bitrate_kbps || 1800), 0)
  const ceiling = maxKbps > 0 ? Math.min(configuredMax, maxKbps) : configuredMax
  const controller = new DelayController({ initialKbps: initialKbps > 0 ? initialKbps : 3000, maxKbps: ceiling, now, probeSession: session })
  const byPort = new Map(streams.map((s) => [s.udp_port, s]))
  const get = (id) => states.get(id) || { state: null, priority: streams.find((s) => s.stream_id === id)?.priority || 'low' }
  function command(msg) {
    const stream = streams.find((s) => s.stream_id === msg.stream_id)
    if (!stream || typeof msg.enabled !== 'boolean' || !['high', 'low'].includes(msg.priority)) return false
    const cmd = { session, request: ++request >>> 0, port: stream.udp_port, enabled: msg.enabled, priority: msg.priority }
    controller.configurationChanged()
    pending.set(stream.stream_id, { cmd, sent: -Infinity, attempts: 0, deadline: now() + 4000 })
    tickCommands()
    return true
  }
  function tickCommands() {
    for (const [id, item] of pending) {
      if (now() - item.sent < 500) continue
      if (item.attempts >= 6 || now() >= item.deadline) {
        pending.delete(id); broadcast({ type: 'video_command_result', stream_id: id, success: false, message: 'No command acknowledgement' }); continue
      }
      // A full serial queue must not accumulate old video commands.
      if (writeFrame(encodeFrame(VIDEO_SET, nextSeq(), setPayload(item.cmd)), 'video') !== false) {
        item.sent = now(); item.attempts++
      }
    }
  }
  function handleState(payload) {
    const state = decodeState(payload)
    const stream = state && byPort.get(state.port)
    if (!stream) return
    const previous = states.get(stream.stream_id)
    if (previous && previous.boot === state.boot && previous.session === state.session && newer(previous.request, state.request)) return
    if (previous && ['enabled', 'priority'].some((key) => previous[key] !== state[key])) controller.configurationChanged()
    else if (previous && ['width', 'height', 'fps'].some((key) => previous[key] !== state[key])) controller.configurationChanged(stream.stream_id)
    states.set(stream.stream_id, state)
    const item = pending.get(stream.stream_id)
    if (item && state.session === session && state.request === item.cmd.request) {
      pending.delete(stream.stream_id)
      broadcast({ type: 'video_command_result', stream_id: stream.stream_id, success: state.result === 0,
        message: state.result === 1 ? 'Video configuration is locked by a latency trial' : state.result === 2 ? 'Command rejected' : '' })
    }
    broadcast({ type: 'video_state', stream_id: stream.stream_id, ...state })
  }
  function tick() {
    tickCommands()
    // Different receiver pipes can deliver out of order. GCC must see the
    // actual receive order, not Node.js pipe callback order.
    const batch = observations.sort((a, b) => a.p.arrival_us - b.p.arrival_us)
    observations = []
    const newest = batch.at(-1)?.p.arrival_us || 0
    const at = now()
    for (const { id, p } of batch) {
      const observation = { ...p, received_at_ms: at - (newest - p.arrival_us) / 1000 }
      controller.observe(id, observation)
    }
    const active = [...states.values()].some((s) => s.state === 'ON')
    const demand = streams.reduce((total, stream) => total +
      (states.get(stream.stream_id)?.state === 'ON' ? stream.encoder.bitrate_kbps : 0), 0)
    let metrics = controller.update(active, demand || ceiling)
    if (active) writeFrame(encodeFrame(VIDEO_FEEDBACK, nextSeq(), feedbackPayload(session, ++feedback, metrics)), 'video')
    // Browser telemetry uses the existing receiver observations and rover
    // reports. It adds no radio polls or XBee packets and does not affect GCC.
    if (at - lastMetricsAt >= 1000) {
      const classes = {
        high: { budget_kbps: states.size ? 0 : null, receive_kbps: 0, utilization_pct: null, streams: 0 },
        low: { budget_kbps: states.size ? 0 : null, receive_kbps: 0, utilization_pct: null, streams: 0 },
      }
      for (const stream of streams) {
        const state = states.get(stream.stream_id)
        const group = classes[state?.priority || stream.priority || 'low']
        group.receive_kbps += metrics.receive_by_stream_kbps[stream.stream_id] || 0
        if (state?.state === 'ON') {
          // The rover reports floor(0.85 * wire allocation). Recover its wire
          // budget to within 1.2 kbps without changing the XBee wire format.
          group.budget_kbps += state.bitrate_kbps / .85
          group.streams++
        }
      }
      for (const group of Object.values(classes))
        if (group.budget_kbps > 0) group.utilization_pct = 100 * group.receive_kbps / group.budget_kbps
      latestMetrics = { type: 'video_link_metrics', updated_at_ms: Date.now(),
        budget_kbps: metrics.budget_kbps, receive_kbps: metrics.receive_kbps,
        network_delay_ms: metrics.network_delay_ms, network_delay_p95_ms: metrics.network_delay_p95_ms,
        network_delay_min_ms: metrics.network_delay_min_ms,
        network_delay_samples: metrics.network_delay_samples,
        queue_delay_ms: metrics.receive_kbps > 0 ? metrics.queue_ms : null,
        loss_pct: metrics.loss * 100, trend_ms_s: metrics.trend_ms_s, classes }
      lastMetricsAt = at
      broadcast(latestMetrics)
    }
  }
  return { command, handleState, get, requestKeyframe, observe: (id, p) => {
      observations.push({ id, p })
      if (observations.length > 8192) { observations = []; controller.resetTrend() }
    },
    start() { if (!timer) { timer = setInterval(tick, 200); timer.unref?.() } },
    stop() { clearInterval(timer); timer = null; pending.clear() },
    tick,
    getMetrics: () => latestMetrics,
  }
}
module.exports = { createVideoControl }
