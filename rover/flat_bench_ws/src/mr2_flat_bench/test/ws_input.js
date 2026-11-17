// Diagnostic client using the dashboard's existing JSON/WebSocket protocol.
const { createRequire } = require('node:module')
const readline = require('node:readline')
const rootRequire = createRequire('/home/mr2/mr2-stack/base/gateway/package.json')
const WebSocket = rootRequire('ws')
const ws = new WebSocket(process.argv[2] || 'wss://10.42.0.1/xbee-ws', { rejectUnauthorized: false })
let value = 0, lateral = 0, paused = false
const counts = {}
function drive() {
  if (ws.readyState === WebSocket.OPEN && !paused) ws.send(JSON.stringify({
    type: 'cmd_drive', linear_x_m_s: value, linear_y_m_s: lateral, angular_z_rad_s: 0,
  }))
}
const timer = setInterval(drive, 50)
const heartbeat = setInterval(() => {
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'heartbeat' }))
}, 250)
ws.on('open', () => console.log(JSON.stringify({ event: 'open' })))
ws.on('message', data => {
  try { const msg = JSON.parse(data); counts[msg.type] = (counts[msg.type] || 0) + 1 } catch {}
})
ws.on('error', error => { console.error(error.message); process.exitCode = 1 })
ws.on('close', () => { clearInterval(timer); clearInterval(heartbeat); console.log(JSON.stringify({ event: 'closed', counts })) })
readline.createInterface({ input: process.stdin }).on('line', line => {
  const msg = JSON.parse(line)
  if (msg.x !== undefined) value = msg.x
  if (msg.y !== undefined) lateral = msg.y
  if (msg.paused !== undefined) paused = msg.paused
  if (msg.estop && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({
    type: 'mission_control', command: 3, clear_costmap: true, mission_id: 0,
  }))
  drive()
})
process.on('SIGINT', () => {
  value = lateral = 0; paused = false; drive()
  clearInterval(timer); clearInterval(heartbeat)
  setTimeout(() => ws.close(), 100)
})
