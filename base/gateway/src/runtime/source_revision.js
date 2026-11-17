'use strict'

const { execFileSync } = require('node:child_process')
const path = require('node:path')

// Capture once at startup: changing the checkout must not make an old process
// appear to be running the new revision in deployment checks.
function readSourceRevision() {
  try {
    return execFileSync('git', ['rev-parse', 'HEAD'], {
      cwd: path.resolve(__dirname, '../../../..'),
      encoding: 'utf8',
      timeout: 2000,
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim()
  } catch (_) {
    return null
  }
}

module.exports = { readSourceRevision }
