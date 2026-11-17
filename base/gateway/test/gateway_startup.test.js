'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const net = require('node:net')
const path = require('node:path')
const { spawn } = require('node:child_process')
const { once } = require('node:events')

test('occupied gateway port exits cleanly before starting video or serial devices', { timeout: 10000 }, async () => {
  const blocker = net.createServer()
  blocker.listen(0, '127.0.0.1')
  await once(blocker, 'listening')
  let child
  try {
    const root = path.resolve(__dirname, '..')
    child = spawn(process.execPath, ['index.js', '--gateway-host', '127.0.0.1', '--gateway-port', String(blocker.address().port)], {
      cwd: root, env: process.env,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let stdout = '', stderr = ''
    child.stdout.on('data', (data) => { stdout += data })
    child.stderr.on('data', (data) => { stderr += data })
    const [code, signal] = await once(child, 'close')
    assert.equal(code, 1, stderr)
    assert.equal(signal, null)
    assert.match(stderr, /already in use.*Stop the existing gateway/)
    assert.doesNotMatch(stderr, /Unhandled|Startup cleanup failed/)
    assert.equal(stdout, '', 'Failed startup must not start video/serial/ROS/MAVProxy producers')
    assert.equal(blocker.listening, true)
  } finally {
    if (child && child.exitCode === null) child.kill('SIGKILL')
    await new Promise((resolve) => blocker.close(resolve))
  }
})
