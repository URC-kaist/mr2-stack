'use strict'

const { createHash } = require('node:crypto')
const { splitAnnexBNalus, stripStartCode } = require('./h264')

const isVcl = (type) => type >= 1 && type <= 5

function accessUnitFingerprint(payload) {
  const hash = createHash('sha256')
  let count = 0
  for (const nal of splitAnnexBNalus(payload, true).nals) {
    const body = stripStartCode(nal)
    if (!isVcl(body[0] & 0x1f)) continue
    hash.update(body)
    count += 1
  }
  return count ? hash.digest('hex') : null
}

// RFC 6184 sections 5.6–5.8: single NAL, STAP-A and FU-A (non-interleaved
// mode). Hash the unchanged VCL bytes, ignoring SPS/PPS/AUD inserted by
// h264parse. Never assign timing to incomplete/unsupported packetizations.
class RtpFrameFingerprint {
  constructor() { this.reset() }

  reset() { this.frame = null }

  push(packet) {
    const { payload, sequence, ssrc, rtp_timestamp: timestamp } = packet
    let frame = this.frame
    if (!frame || frame.ssrc !== ssrc || frame.timestamp !== timestamp) {
      frame = this.frame = {
        ssrc, timestamp, next: sequence, hash: createHash('sha256'),
        valid: true, hasVcl: false, fragment: null,
      }
    }
    if (sequence !== frame.next) frame.valid = false
    frame.next = (sequence + 1) & 0xffff
    const nal = (bytes) => {
      if (!bytes.length) { frame.valid = false; return }
      if (isVcl(bytes[0] & 0x1f)) {
        frame.hash.update(bytes)
        frame.hasVcl = true
      }
    }
    if (!payload?.length) {
      frame.valid = false
    } else {
      const type = payload[0] & 0x1f
      if (type >= 1 && type <= 23) {
        if (frame.fragment !== null) frame.valid = false
        nal(payload)
      } else if (type === 24) {
        if (frame.fragment !== null) frame.valid = false
        let offset = 1
        while (offset + 2 <= payload.length) {
          const size = payload.readUInt16BE(offset)
          offset += 2
          if (!size || offset + size > payload.length) { frame.valid = false; break }
          const body = payload.subarray(offset, offset + size)
          if (!(body[0] & 0x1f) || (body[0] & 0x1f) > 23) frame.valid = false
          nal(body)
          offset += size
        }
        if (offset !== payload.length) frame.valid = false
      } else if (type === 28 && payload.length >= 2) {
        const header = (payload[0] & 0xe0) | (payload[1] & 0x1f)
        const start = (payload[1] & 0x80) !== 0
        const end = (payload[1] & 0x40) !== 0
        if (!(header & 0x1f) || (header & 0x1f) > 23) frame.valid = false
        if (start && end) frame.valid = false
        if (start) {
          if (frame.fragment !== null) frame.valid = false
          frame.fragment = header
          nal(Buffer.from([header]))
        } else if (frame.fragment !== header) {
          frame.valid = false
        }
        if (isVcl(header & 0x1f)) frame.hash.update(payload.subarray(2))
        if (end) frame.fragment = null
      } else {
        frame.valid = false
      }
    }
    if (!packet.marker) return null
    this.frame = null
    return frame.valid && frame.hasVcl && frame.fragment === null
      ? frame.hash.digest('hex') : null
  }
}

module.exports = { accessUnitFingerprint, RtpFrameFingerprint }
