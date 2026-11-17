const assert = require('node:assert/strict')
const fs = require('node:fs')
const { spawn } = require('node:child_process')
const { setTimeout: delay } = require('node:timers/promises')
const WebSocket = require('/home/mr2/mr2-stack/base/gateway/node_modules/ws')
const out = '/tmp/mr2-link-chart-browser'
fs.mkdirSync(out, { recursive: true })
const chrome = spawn('/snap/bin/chromium', ['--headless=new', '--no-sandbox', '--disable-gpu',
  '--disable-dev-shm-usage', '--ignore-certificate-errors', '--remote-debugging-port=9342',
  '--user-data-dir=' + out + '/chrome', 'https://127.0.0.1/'],
  { stdio: ['ignore', 'ignore', fs.openSync(out + '/chromium.log', 'w')] })
let cdp, seq = 0
const pending = new Map(), report = {}, errors = []
function request(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++seq; pending.set(id, { resolve, reject }); cdp.send(JSON.stringify({ id, method, params }))
  })
}
async function evaluate(expression) {
  const r = await request('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails))
  return r.result.value
}
async function screenshot(name) {
  const r = await request('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(out + '/' + name + '.png', Buffer.from(r.data, 'base64'))
}
async function main() {
  for (let i = 0; i < 70; i++) {
    try {
      const pages = await (await fetch('http://127.0.0.1:9342/json')).json()
      const page = pages.find((p) => p.type === 'page')
      if (page) { cdp = new WebSocket(page.webSocketDebuggerUrl); break }
    } catch {}
    await delay(200)
  }
  assert(cdp)
  await new Promise((resolve, reject) => { cdp.on('open', resolve); cdp.on('error', reject) })
  cdp.on('message', (b) => {
    const m = JSON.parse(b)
    if (m.id) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(Error(JSON.stringify(m.error))) : p.resolve(m.result) }
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.text)
  })
  await request('Runtime.enable')
  await request('Page.enable')
  await request('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1100, deviceScaleFactor: 1, mobile: false })
  // Passively capture gateway telemetry in this isolated test browser.
  await request('Page.addScriptToEvaluateOnNewDocument', { source: `
    window.__metrics = []; window.__radios = [];
    const NativeSocket = window.WebSocket;
    window.WebSocket = class extends NativeSocket {
      constructor(...args) { super(...args); this.addEventListener('message', e => {
        if (typeof e.data !== 'string') return;
        try { const m = JSON.parse(e.data);
          if (m.type === 'video_link_metrics') window.__metrics.push(m);
          if (m.type === 'rocket_m2_status') window.__radios.push(m);
        } catch {}
      }); }
    };
  ` })
  await request('Page.reload', { ignoreCache: true }); await delay(1800)
  await evaluate(`[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Live Feed').click()`)
  await delay(15000)
  report.page = await evaluate('({url:location.href,title:document.title,hasMetrics:Array.isArray(window.__metrics)})')
  report.metrics = await evaluate('window.__metrics.slice(-3)')
  report.radios = await evaluate('window.__radios.slice(-4)')
  report.firstTile = await evaluate("document.querySelector('.video-feed-grid')?.firstElementChild?.className")
  assert(report.firstTile.includes('video-link-card'), report.firstTile)
  assert(report.metrics.length === 3)
  for (const m of report.metrics) {
    assert(Math.abs(m.receive_kbps - m.classes.high.receive_kbps - m.classes.low.receive_kbps) < 1e-6)
    for (const group of Object.values(m.classes)) {
      if (group.budget_kbps > 0)
        assert(Math.abs(group.utilization_pct - 100 * group.receive_kbps / group.budget_kbps) < 1e-6)
    }
  }
  report.traffic = await evaluate(`({ text: document.querySelector('.video-link-card').innerText,
    paths: [...document.querySelectorAll('.video-link-plot path')].map(p => p.getAttribute('d')),
    rows: [...document.querySelectorAll('.video-link-classes tbody tr')].map(r => r.innerText),
    overflow: document.querySelector('.video-link-body').scrollWidth > document.querySelector('.video-link-body').clientWidth })`)
  assert(!report.traffic.text.includes('NaN') && !report.traffic.text.includes('Infinity'))
  assert(report.traffic.paths.every((p) => p.includes('L')))
  assert(!report.traffic.overflow)
  assert(!report.traffic.rows.some((r) => r.includes('—')))
  await screenshot('traffic')
  await evaluate(`[...document.querySelectorAll('.video-link-modes button')].find(b => b.textContent === 'Signal').click()`)
  report.signal = await evaluate(`({ text: document.querySelector('.video-link-card').innerText,
    paths: [...document.querySelectorAll('.video-link-plot path')].map(p => p.getAttribute('d')),
    labels: [...document.querySelectorAll('.video-link-plot text')].map(n => n.textContent) })`)
  assert(report.signal.paths.every((p) => p.includes('L')))
  assert(report.signal.text.includes('dBm'))
  const bounds = await evaluate(`(()=>{const b=document.querySelector('.video-link-plot svg').getBoundingClientRect();return {x:b.x+b.width*.95,y:b.y+b.height*.5}})()`)
  await request('Input.dispatchMouseEvent', { type: 'mouseMoved', x: bounds.x, y: bounds.y })
  await delay(150)
  report.tooltip = await evaluate("document.querySelector('.video-link-tooltip')?.innerText")
  assert(report.tooltip?.includes('Base RX'))
  await screenshot('signal')
  await request('Emulation.setDeviceMetricsOverride', { width: 900, height: 1100, deviceScaleFactor: 1, mobile: false })
  await delay(500)
  report.compact = await evaluate(`(()=>{const b=document.querySelector('.video-link-body');return {width:b.clientWidth,scrollWidth:b.scrollWidth,height:b.clientHeight,scrollHeight:b.scrollHeight}})()`)
  assert(report.compact.scrollWidth <= report.compact.width)
  assert(report.compact.scrollHeight <= report.compact.height + 1)
  await screenshot('compact')
  assert.deepEqual(errors, [])
  report.passed = true
}
main().catch((e) => { report.passed = false; report.error = e.stack; process.exitCode = 1 }).finally(() => {
  fs.writeFileSync(out + '/result.json', JSON.stringify(report, null, 2))
  console.log(JSON.stringify(report, null, 2)); cdp?.close(); chrome.kill('SIGTERM')
})
