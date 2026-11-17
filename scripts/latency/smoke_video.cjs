#!/usr/bin/env node
'use strict'

// Four synthetic RTP feeds -> actual GStreamer gateway -> timed WebSocket chunks.
// Only loopback and ephemeral ports are used; no camera, CAN or serial devices.
const assert = require('node:assert/strict')
const dgram = require('node:dgram')
const http = require('node:http')
const { spawn, execFileSync } = require('node:child_process')
const { once } = require('node:events')
const { setTimeout: delay } = require('node:timers/promises')
const path = require('node:path')
const WebSocket = require('../../base/gateway/node_modules/ws')
const { createVideoGateway } = require('../../base/gateway/src/video/service')
const { createWsRouteRegistry } = require('../../base/gateway/src/runtime/ws_route_registry')
const { loadVideoConfig } = require('../../base/gateway/src/video/stream_config')

async function main() {
  const lateJoin = process.argv.includes('--late-join')
  for (const plugin of ['videotestsrc', 'x264enc', 'rtph264pay', 'rtpstreampay']) {
    execFileSync('gst-inspect-1.0', [plugin], { stdio: 'ignore', timeout: 5000 })
  }
  const videoConfig = loadVideoConfig(path.resolve(__dirname,
    '../../rover/ros2_ws/src/mr2_launch/config/video_streams_flat.json'))
  const server = http.createServer((_req, res) => { res.writeHead(404); res.end() })
  const routes = createWsRouteRegistry({ server })
  const reservations = []
  const senders = []
  const errors = []
  const counts = Object.fromEntries(videoConfig.streams.map((s) => [s.stream_id,
    { configs: 0, timed_chunks: 0, fallback_chunks: 0, last_timestamp: 0 }]))
  let gateway
  let ws
  try {
    for (const stream of videoConfig.streams) {
      const socket = dgram.createSocket('udp4')
      reservations.push(socket)
      socket.bind(0, '127.0.0.1')
      await once(socket, 'listening')
      stream.udp_port = socket.address().port
    }
    gateway = createVideoGateway({ videoConfig, routeRegistry: routes,
      log: (line) => { if (/error|exited/i.test(line)) errors.push(line) } })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    ws = new WebSocket(`ws://127.0.0.1:${server.address().port}/video-ws`)
    ws.on('message', (data) => {
      try {
        const type = data.readUInt8(0)
        const size = data.readUInt16LE(1)
        const offset = type === 1 ? 17 : type === 3 ? 34 : 16
        const id = data.subarray(offset, offset + size).toString('utf8')
        const count = counts[id]
        assert.ok(count, `unexpected stream ${id}`)
        if (type === 1) {
          assert.equal(data.readUInt16LE(13), 320)
          assert.equal(data.readUInt16LE(15), 180)
          count.configs += 1
        } else if (type === 3) {
          const timestamp = Number(data.readBigUInt64LE(4))
          assert.ok(timestamp > count.last_timestamp, 'decode timestamps must advance')
          assert.ok(data.readBigUInt64LE(22) > 0n, 'missing base ingest timestamp')
          assert.equal(data.length, 34 + size + data.readUInt32LE(30))
          count.last_timestamp = timestamp
          count.timed_chunks += 1
        } else {
          assert.equal(type, 2)
          count.fallback_chunks += 1
        }
      } catch (error) { errors.push(error.message) }
    })
    await once(ws, 'open')
    for (const stream of videoConfig.streams) {
      ws.send(JSON.stringify({ type: 'subscribe', stream_id: stream.stream_id }))
    }
    if (!lateJoin) {
      for (const socket of reservations.splice(0)) socket.close()
      gateway.start()
      await delay(500)
    }
    for (const [index, stream] of videoConfig.streams.entries()) {
      const child = spawn('gst-launch-1.0', ['-q',
        'videotestsrc', 'is-live=true', `num-buffers=${lateJoin ? 240 : 120}`, `pattern=${index}`,
        '!', 'video/x-raw,width=320,height=180,framerate=30/1',
        '!', 'x264enc', 'tune=zerolatency', 'speed-preset=ultrafast', 'bitrate=1800', `key-int-max=${lateJoin ? 120 : 30}`,
        '!', 'rtph264pay', 'pt=96', 'config-interval=1',
        '!', 'udpsink', 'host=127.0.0.1', `port=${stream.udp_port}`, 'sync=false'],
      { stdio: ['ignore', 'ignore', 'pipe'] })
      child.stderr.on('data', (data) => errors.push(data.toString().trim()))
      child.on('error', (error) => errors.push(error.message))
      senders.push(child)
    }
    if (lateJoin) {
      // Drop startup SPS/IDR, then join mid-GOP. The H.264 branch must wait for
      // a later keyframe without deadlocking the parallel RTP metadata branch.
      await delay(1500)
      for (const socket of reservations.splice(0)) socket.close()
      gateway.start()
    }
    const deadline = Date.now() + 10_000
    while (Date.now() < deadline && !errors.length &&
      !Object.values(counts).every((s) => s.timed_chunks >= 60)) await delay(50)
    assert.deepEqual(errors, [])
    for (const [id, count] of Object.entries(counts)) {
      assert.ok(count.configs > 0, `${id}: codec config missing`)
      assert.ok(count.timed_chunks >= 60, `${id}: only ${count.timed_chunks} timed frames`)
    }
    assert.ok(gateway.getBrowserStreams().every((s) => s.available &&
      s.encoded_width === 320 && s.encoded_height === 180))
    console.log(JSON.stringify({ passed: true, late_join: lateJoin, scope: 'Synthetic RTP to WebSocket; no browser rendering or radio link', streams: counts }, null, 2))
  } finally {
    for (const socket of reservations) socket.close()
    for (const child of senders) child.kill('SIGTERM')
    ws?.terminate()
    gateway?.stop()
    routes.close()
    server.close()
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1 })
