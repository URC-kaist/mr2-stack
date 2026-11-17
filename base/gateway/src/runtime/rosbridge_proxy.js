'use strict'
const { WebSocket, WebSocketServer } = require('ws')

// Keep the browser's same-origin URL while the upstream rover is chosen by CLI.
function createRosbridgeProxy({ routeRegistry, host, port = 9090 }) {
  const path = '/rosbridge-ws'
  const wss = new WebSocketServer({ noServer: true, maxPayload: 16 * 1024 * 1024 })
  const peers = new Set()
  routeRegistry.register(path, wss)
  wss.on('connection', client => {
    const upstream = new WebSocket(`ws://${host}:${port}`, { handshakeTimeout: 5000 })
    peers.add(upstream)
    let pending = [], bytes = 0
    const close = () => { pending = []; peers.delete(upstream); upstream.terminate(); client.terminate() }
    client.on('message', (data, binary) => {
      if (upstream.readyState === WebSocket.OPEN) {
        if (upstream.bufferedAmount > 1024 * 1024) return close()
        upstream.send(data, { binary })
      } else if (upstream.readyState === WebSocket.CONNECTING) {
        bytes += data.length
        if (bytes > 1024 * 1024) return close()
        pending.push({ data, binary })
      }
    })
    upstream.on('open', () => {
      for (const item of pending) upstream.send(item.data, { binary: item.binary })
      pending = []; bytes = 0
    })
    upstream.on('message', (data, binary) => {
      if (client.readyState !== WebSocket.OPEN) return
      if (client.bufferedAmount > 1024 * 1024) return close()
      client.send(data, { binary })
    })
    for (const peer of [client, upstream]) {
      peer.on('error', close)
      peer.on('close', close)
    }
  })
  return { close() {
    routeRegistry.unregister(path)
    for (const peer of peers) peer.terminate()
    for (const client of wss.clients) client.terminate()
    wss.close()
  } }
}
module.exports = { createRosbridgeProxy }
