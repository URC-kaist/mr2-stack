'use strict'

const { loadGatewayEnv, parseGatewayConfig } = require('./src/config')
const { createGatewayApp } = require('./src/app/create_gateway_app')

loadGatewayEnv(__dirname)

const config = parseGatewayConfig(process.argv.slice(2), process.env)
const app = createGatewayApp({
  config,
  env: process.env,
})

let shuttingDown = false

async function shutdown(signal) {
  if (shuttingDown) return
  shuttingDown = true

  // eslint-disable-next-line no-console
  console.log(`[gateway] ${signal} received, shutting down`)

  try {
    await app.stop()
    process.exit(0)
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error(`[gateway] Shutdown failed: ${err.message || err}`)
    process.exit(1)
  }
}

process.on('SIGINT', () => {
  shutdown('SIGINT')
})

process.on('SIGTERM', () => {
  shutdown('SIGTERM')
})

app.start().catch(async (err) => {
  // eslint-disable-next-line no-console
  console.error(err.code === 'EADDRINUSE'
    ? `[gateway] ${config.host}:${config.port} is already in use. Stop the existing gateway before starting another instance.`
    : `[gateway] Startup failed: ${err.message || err}`)
  try {
    await app.stop()
  } catch (cleanupError) {
    // eslint-disable-next-line no-console
    console.error(`[gateway] Startup cleanup failed: ${cleanupError.message || cleanupError}`)
  }
  process.exit(1)
})
