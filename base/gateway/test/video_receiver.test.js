'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('events')

const { buildGstReceiveArgs, createVideoStreamReceiver } = require('../src/video/receiver')

function rtpStreamPacket(sequence = 1, timestamp = 90000, ssrc = 0x10203040, payload = Buffer.from([0x65, 0xe0])) {
  const rtp = Buffer.alloc(12 + payload.length)
  rtp.writeUInt8(0x80, 0)
  rtp.writeUInt8(0x80 | 96, 1)
  rtp.writeUInt16BE(sequence, 2)
  rtp.writeUInt32BE(timestamp, 4)
  rtp.writeUInt32BE(ssrc, 8)
  payload.copy(rtp, 12)
  const framed = Buffer.alloc(rtp.length + 2)
  framed.writeUInt16BE(rtp.length, 0)
  rtp.copy(framed, 2)
  return framed
}

function createFakeChild() {
  const child = new EventEmitter()
  child.stdout = new EventEmitter()
  child.stderr = new EventEmitter()
  child.stdio = [null, child.stdout, child.stderr, new EventEmitter()]
  child.kill = () => {}
  return child
}

test('native receiver pairs a complete low-FPS frame without waiting for another frame', () => {
  const units = []; let child, args
  const receiver = createVideoStreamReceiver({
    stream: {stream_id:'rgbd',udp_port:5022}, gstBinary:'/native/video_receiver',
    spawnImpl: (_binary, argv) => {args=argv;return (child=createFakeChild())},
    onAccessUnit: (_id, unit) => units.push(unit),
  })
  receiver.start()
  assert(args.includes('name=video_output'))
  assert(!args.includes('fd=1'))
  child.stdio[3].emit('data', rtpStreamPacket())
  const payload=Buffer.from([0,0,0,1,0x65,0xe0]), framed=Buffer.alloc(4+payload.length)
  framed.writeUInt32BE(payload.length);payload.copy(framed,4)
  child.stdout.emit('data',framed.subarray(0,5));assert.equal(units.length,0)
  child.stdout.emit('data',framed.subarray(5));assert.equal(units.length,1)
  assert.equal(units[0].correlation.rtp_timestamp,90000)
  assert.equal(units[0].key,true)
  receiver.stop()
})

test('native metadata and video arrive atomically without a second RTP pipe or timeout', () => {
  const units = []; let child
  const receiver = createVideoStreamReceiver({ stream: { stream_id:'rgbd',udp_port:5022 }, gstBinary:'/native/video_receiver',
    spawnImpl: () => (child=createFakeChild()), onAccessUnit: (_id,u) => units.push(u) })
  receiver.start()
  const payload=Buffer.from([0,0,0,1,0x65,0xe0]), frame=Buffer.alloc(28+payload.length)
  frame.writeUInt32BE((0x80000000+payload.length)>>>0); frame.writeUInt32BE(1,4)
  frame.writeUInt32BE(123,8); frame.writeUInt32BE(90000,12); frame.writeUInt16BE(7,16); frame.writeBigUInt64BE(123456n,20); payload.copy(frame,28)
  child.stdout.emit('data',frame.subarray(0,27));assert.equal(units.length,0)
  child.stdout.emit('data',frame.subarray(27));assert.equal(units.length,1)
  assert.deepEqual(units[0].correlation,{ssrc:123,rtp_timestamp:90000,marker_sequence:7});assert.equal(units[0].timestamp_us,123456)
  frame.writeUInt32BE(0,4); child.stdout.emit('data',frame);assert.equal(units.length,2);assert.equal(units[1].correlation,undefined)
  receiver.stop()
})

test('buildGstReceiveArgs uses the configured jitter latency and supported drop policy', () => {
  const args = buildGstReceiveArgs(
    {
      stream_id: 'front_nav_cam',
      udp_port: 5000,
    },
    {
      jitterLatencyMs: 40,
    }
  )

  assert.deepEqual(args.slice(0, 8), [
    '-q',
    'udpsrc',
    'port=5000',
    'caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000',
    '!',
    'rtpjitterbuffer',
    'latency=40',
    'drop-on-latency=true',
  ])
  for (const fd of ['fd=1', 'fd=3']) {
    const at = args.indexOf(fd)
    assert.deepEqual(args.slice(at + 1, at + 3), ['sync=false', 'async=false'])
  }
})

test('createVideoStreamReceiver drops trailing partial access units when the child exits', () => {
  let child = null
  const accessUnits = []
  const receiver = createVideoStreamReceiver({
    stream: {
      stream_id: 'front_nav_cam',
      udp_port: 5000,
    },
    idleFlushMs: 100,
    onAccessUnit: (_streamId, accessUnit) => accessUnits.push(accessUnit),
    restartMs: 1000,
    spawnImpl: () => {
      child = createFakeChild()
      return child
    },
  })

  receiver.start()
  child.stdout.emit('data', Buffer.from([0x00, 0x00, 0x00, 0x01, 0x65, 0x88, 0x84]))
  child.emit('exit', 1, null)
  receiver.stop()

  assert.deepEqual(accessUnits, [])
})

test('createVideoStreamReceiver marks streams unavailable after inactivity', async () => {
  let child = null
  const availability = []
  const receiver = createVideoStreamReceiver({
    availabilityStaleMs: 20,
    stream: {
      stream_id: 'front_nav_cam',
      udp_port: 5000,
    },
    onAvailabilityChange: (_streamId, nextAvailable) => availability.push(nextAvailable),
    restartMs: 1000,
    spawnImpl: () => {
      child = createFakeChild()
      return child
    },
  })

  receiver.start()
  child.stdio[3].emit('data', rtpStreamPacket())
  child.stdout.emit(
    'data',
    Buffer.from([
      0x00, 0x00, 0x00, 0x01, 0x65, 0xe0,
      0x00, 0x00, 0x00, 0x01, 0x41, 0xe0,
      0x00, 0x00, 0x00, 0x01, 0x41, 0xe0,
    ])
  )

  await new Promise((resolve) => setTimeout(resolve, 40))
  receiver.stop()

  assert.deepEqual(availability, [true, false])
})

test('createVideoStreamReceiver pairs marker metadata regardless of fd event order', () => {
  let child = null
  const accessUnits = []
  const receiver = createVideoStreamReceiver({
    stream: { stream_id: 'front_nav_cam', udp_port: 5000 },
    onAccessUnit: (_streamId, accessUnit) => accessUnits.push(accessUnit),
    restartMs: 1000,
    spawnImpl: () => {
      child = createFakeChild()
      return child
    },
  })
  receiver.start()
  child.stdout.emit(
    'data',
    Buffer.from([
      0x00, 0x00, 0x00, 0x01, 0x65, 0xe0,
      0x00, 0x00, 0x00, 0x01, 0x41, 0xe0,
      0x00, 0x00, 0x00, 0x01, 0x41, 0xe0,
    ])
  )
  assert.equal(accessUnits.length, 0)
  child.stdio[3].emit('data', rtpStreamPacket(77, 123456, 42))
  assert.equal(accessUnits.length, 1)
  assert.deepEqual(accessUnits[0].correlation, {
    marker_sequence: 77,
    rtp_timestamp: 123456,
    ssrc: 42,
  })
  receiver.stop()
})


test('late join does not assign an earlier discarded delta marker to an IDR', () => {
  const child = createFakeChild()
  const frames = []
  const receiver = createVideoStreamReceiver({
    stream: { stream_id: 'flat_cam_1', udp_port: 5010 },
    spawnImpl: () => child,
    onAccessUnit: (_id, frame) => frames.push(frame),
  })
  try {
    receiver.start()
    child.stdio[3].emit('data', Buffer.concat([
      rtpStreamPacket(70, 90000, 42, Buffer.from([0x41, 0xe1])),
      rtpStreamPacket(71, 93000, 42, Buffer.from([0x41, 0xe2])),
      rtpStreamPacket(72, 96000, 42, Buffer.from([0x65, 0xe0])),
    ]))
    child.stdout.emit('data', Buffer.from([
      0, 0, 0, 1, 0x65, 0xe0,
      0, 0, 0, 1, 0x41, 0xe0,
      0, 0, 0, 1, 0x41, 0xe0,
    ]))
    assert.equal(frames.length, 1)
    assert.equal(frames[0].correlation.marker_sequence, 72)
    assert.equal(frames[0].correlation.rtp_timestamp, 96000)
  } finally { receiver.stop() }
})

test('stop cancels pending uncorrelated frame output', async () => {
  const child = createFakeChild()
  const frames = []
  const receiver = createVideoStreamReceiver({
    stream: { stream_id: 'flat_cam_1', udp_port: 5010 },
    spawnImpl: () => child,
    onAccessUnit: (_id, frame) => frames.push(frame),
  })
  receiver.start()
  child.stdout.emit('data', Buffer.from([
    0, 0, 0, 1, 0x65, 0xe0,
    0, 0, 0, 1, 0x41, 0xe0,
    0, 0, 0, 1, 0x41, 0xe0,
  ]))
  receiver.stop()
  await new Promise(resolve => setTimeout(resolve, 150))
  assert.equal(frames.length, 0)
})

test('an unmatched access unit does not starve later verified frames', () => {
  const child = createFakeChild()
  const frames = []
  const receiver = createVideoStreamReceiver({
    stream: { stream_id: 'flat_cam_1', udp_port: 5010 },
    spawnImpl: () => child,
    onAccessUnit: (_id, frame) => frames.push(frame),
  })
  try {
    receiver.start()
    child.stdio[3].emit('data', rtpStreamPacket(72, 96000, 42, Buffer.from([0x41, 0xe2])))
    child.stdout.emit('data', Buffer.from([
      0, 0, 0, 1, 0x65, 0xe1,
      0, 0, 0, 1, 0x41, 0xe2,
      0, 0, 0, 1, 0x41, 0xe3,
      0, 0, 0, 1, 0x41, 0xe4,
    ]))
    assert.equal(frames.length, 2)
    assert.equal(frames[0].correlation, undefined)
    assert.equal(frames[1].correlation.marker_sequence, 72)
  } finally { receiver.stop() }
})
