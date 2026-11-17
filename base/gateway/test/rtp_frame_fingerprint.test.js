'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const { accessUnitFingerprint, RtpFrameFingerprint } = require('../src/video/rtp_frame_fingerprint')
const nal = Buffer.from([0x65, 0xe0, 1, 2, 3, 4])
const annexb = (...nals) => Buffer.concat(nals.flatMap(n => [Buffer.from([0, 0, 0, 1]), n]))
const packet = (payload, sequence = 1, marker = true, timestamp = 90000) => ({payload, sequence, marker, ssrc: 42, rtp_timestamp: timestamp})

test('single NAL fingerprint matches VCL content despite inserted parameter sets', () => {
  const expected = accessUnitFingerprint(annexb(Buffer.from([0x67, 1]), nal, Buffer.from([0x09, 0xf0])))
  assert.equal(new RtpFrameFingerprint().push(packet(nal)), expected)
})

test('FU-A reconstruction matches complete NAL and handles sequence wrap', () => {
  const parser = new RtpFrameFingerprint()
  assert.equal(parser.push(packet(Buffer.from([0x7c, 0x85, 0xe0, 1]), 65535, false)), null)
  assert.equal(parser.push(packet(Buffer.from([0x7c, 0x45, 2, 3, 4]), 0)), accessUnitFingerprint(annexb(nal)))
})

test('STAP-A supports multiple slices and ignores non-VCL NALs', () => {
  const second = Buffer.from([0x41, 0xe2])
  const bodies = [Buffer.from([0x67, 1]), nal, second]
  const stap = Buffer.concat([Buffer.from([0x78]), ...bodies.flatMap(n => {
    const length = Buffer.alloc(2); length.writeUInt16BE(n.length); return [length, n]
  })])
  assert.equal(new RtpFrameFingerprint().push(packet(stap)), accessUnitFingerprint(annexb(nal, second)))
})

test('incomplete fragments, packet loss, malformed aggregation and unsupported modes get no timing', () => {
  for (const packets of [
    [packet(Buffer.from([0x7c, 0x45, 1]))],
    [packet(Buffer.from([0x7c, 0x85, 1]), 1, false), packet(Buffer.from([0x7c, 0x45, 2]), 3)],
    [packet(Buffer.from([0x7c, 0x85, 1]))],
    [packet(Buffer.from([0x78, 0, 4, 0x65]))],
    [packet(Buffer.from([0x79, 1, 2]))],
  ]) {
    const parser = new RtpFrameFingerprint()
    for (const value of packets) assert.equal(parser.push(value), null)
    assert.equal(parser.push(packet(nal, 5, true, 93000)), accessUnitFingerprint(annexb(nal)))
  }
})
