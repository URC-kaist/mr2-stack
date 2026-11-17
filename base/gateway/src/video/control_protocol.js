'use strict'

const VIDEO_SET = 0x40
const VIDEO_FEEDBACK = 0x41
const VIDEO_STATE = 0x42
const VIDEO_KEYFRAME = 0x43
const STATES = ['OFF', 'ON', 'NO DEVICE']
const newer = (a, b) => ((a - b) | 0) > 0
function probeToken(session, id) {
  if (!session) return id >>> 0
  let x = (session ^ Math.imul(id, 0x9e3779b1)) >>> 0
  x = Math.imul(x ^ (x >>> 16), 0x7feb352d) >>> 0
  x = Math.imul(x ^ (x >>> 15), 0x846ca68b) >>> 0
  return (x ^ (x >>> 16)) >>> 0
}

function setPayload({ session, request, port, enabled, priority }) {
  const b = Buffer.alloc(12)
  b.writeUInt32LE(session >>> 0, 0)
  b.writeUInt32LE(request >>> 0, 4)
  b.writeUInt16LE(port, 8)
  b[10] = enabled ? 1 : 0
  b[11] = priority === 'high' ? 1 : 0
  return b
}
function feedbackPayload(session, sequence, metrics) {
  const b = Buffer.alloc(metrics.probe_id ? 30 : 22)
  b.writeUInt32LE(session >>> 0, 0)
  b.writeUInt32LE(sequence >>> 0, 4)
  b.writeUInt32LE(Math.round(metrics.budget_kbps), 8)
  b.writeUInt32LE(Math.round(metrics.receive_kbps), 12)
  b.writeInt32LE(Math.round(metrics.trend_ms_s * 1000), 16)
  b.writeUInt16LE(Math.min(1000, Math.round(metrics.loss * 1000)), 20)
  if (metrics.probe_id) {
    b.writeUInt16LE(metrics.probe_id, 22)
    b.writeUInt32LE(Math.round(metrics.probe_kbps), 24)
    b.writeUInt16LE(metrics.probe_duration_ms, 28)
  }
  return b
}
function decodeState(b) {
  if (b.length !== 26 || b[14] > 2 || b[15] > 3 || b[25] > 2) return null
  return {
    boot: b.readUInt32LE(0), session: b.readUInt32LE(4), request: b.readUInt32LE(8),
    port: b.readUInt16LE(12), state: STATES[b[14]], priority: (b[15] & 1) ? 'high' : 'low', enabled: Boolean(b[15] & 2),
    bitrate_kbps: b.readUInt32LE(16), width: b.readUInt16LE(20), height: b.readUInt16LE(22),
    fps: b[24], result: b[25], // 0 accepted, 1 trial lock, 2 rejected
  }
}
module.exports = { VIDEO_SET, VIDEO_FEEDBACK, VIDEO_STATE, VIDEO_KEYFRAME, newer, probeToken, setPayload, feedbackPayload, decodeState }
