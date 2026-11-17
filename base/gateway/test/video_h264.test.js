'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')

const { AnnexBAccessUnitParser, FramedAccessUnitParser, deriveCodecString, parseSpsDimensions } = require('../src/video/h264')

function annexb(bytes) {
  return Buffer.from([0x00, 0x00, 0x00, 0x01, ...bytes])
}

test('native AU framing emits a single frame immediately for every pipe split', () => {
  const payload = Buffer.concat([annexb([0x67, 0x42, 0xe0, 0x1f]), annexb([0x68, 0xce]), annexb([0x65, 0xe0])])
  const wire = Buffer.alloc(4 + payload.length)
  wire.writeUInt32BE(payload.length); payload.copy(wire, 4)
  for (let split = 1; split < wire.length; split++) {
    const parser = new FramedAccessUnitParser()
    assert.deepEqual(parser.push(wire.subarray(0, split)), [])
    const [frame] = parser.push(wire.subarray(split))
    assert.equal(frame.key, true)
    assert.deepEqual(frame.payload, payload)
    assert.deepEqual(parser.flush(), [])
  }
  const parser = new FramedAccessUnitParser()
  assert.equal(parser.push(Buffer.concat([wire, wire])).length, 2)
})

test('native AU framing rejects corrupt lengths and discards incomplete tails', () => {
  for (const length of [0, 16 * 1024 * 1024 + 1]) {
    const header = Buffer.alloc(4); header.writeUInt32BE(length)
    assert.throws(() => new FramedAccessUnitParser().push(header), /invalid native/)
  }
  const parser = new FramedAccessUnitParser()
  assert.deepEqual(parser.push(Buffer.from([0, 0, 0, 8, 0, 0])), [])
  assert.deepEqual(parser.flush(), [])
})

test('AnnexBAccessUnitParser groups SPS/PPS/IDR into one key access unit', () => {
  const parser = new AnnexBAccessUnitParser()
  const sps = annexb([0x67, 0x42, 0xe0, 0x1f])
  const pps = annexb([0x68, 0xce, 0x38, 0x80])
  const idr = annexb([0x65, 0xe0])

  const emitted = parser.push(Buffer.concat([sps, pps, idr]))
  assert.equal(emitted.length, 0)

  const flushed = parser.flush()
  assert.equal(flushed.length, 1)
  assert.equal(flushed[0].key, true)
  assert.equal(flushed[0].delta, false)
  assert.equal(flushed[0].codec, 'avc1.42E01F')
  assert.deepEqual(flushed[0].sps, Buffer.from([0x67, 0x42, 0xe0, 0x1f]))
  assert.deepEqual(flushed[0].pps, Buffer.from([0x68, 0xce, 0x38, 0x80]))
})

test('AnnexBAccessUnitParser splits on new VCL access units', () => {
  const parser = new AnnexBAccessUnitParser()
  const idr = annexb([0x65, 0xe0])
  const deltaOne = annexb([0x41, 0xe0])
  const deltaTwo = annexb([0x41, 0xe0])

  const emitted = parser.push(Buffer.concat([idr, deltaOne, deltaTwo]))
  assert.equal(emitted.length, 1)
  assert.equal(emitted[0].key, true)

  const flushed = parser.flush()
  assert.equal(flushed.length, 2)
  assert.equal(flushed[0].delta, true)
  assert.equal(flushed[1].delta, true)
})

test('deriveCodecString returns null when SPS bytes are incomplete', () => {
  assert.equal(deriveCodecString(Buffer.from([0x67, 0x42, 0xe0])), null)
})

test('parseSpsDimensions derives coded size from SPS bytes', () => {
  const sps = Buffer.from(
    '67f4001e90d9680a03db016a0c0c0c80000003008000001e478b1750',
    'hex'
  )
  assert.deepEqual(parseSpsDimensions(sps), { width: 640, height: 480 })
})
