const assert = require('node:assert/strict')
const fs = require('node:fs')
const { spawn } = require('node:child_process')
const { setTimeout: delay } = require('node:timers/promises')
const WebSocket = require('/home/mr2/mr2-stack/base/gateway/node_modules/ws')
const out = '/tmp/mr2-link-layout-browser'
fs.mkdirSync(out, { recursive: true })
const chrome = spawn('/snap/bin/chromium', ['--headless=new', '--no-sandbox', '--disable-gpu',
  '--disable-dev-shm-usage', '--ignore-certificate-errors', '--remote-debugging-port=9343',
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
  for(let i=0;i<70;i++) {
    try { const pages=await(await fetch('http://127.0.0.1:9343/json')).json();const p=pages.find(p=>p.type==='page');if(p){cdp=new WebSocket(p.webSocketDebuggerUrl);break} } catch {}
    await delay(200)
  }
  assert(cdp)
  await new Promise((r,j)=>{cdp.on('open',r);cdp.on('error',j)})
  cdp.on('message',b=>{const m=JSON.parse(b);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}})
  await request('Page.enable')
  await request('Emulation.setDeviceMetricsOverride',{width:1600,height:1100,deviceScaleFactor:1,mobile:false})
  await request('Page.reload',{ignoreCache:true});await delay(1600)
  await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Live Feed').click()`)
  await delay(1200)
  report.cases=[]
  for(const font of [16,20])for(const width of [1100,1200,1280,1366,1440,1600,1920]) {
    await request('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:false})
    await evaluate(`document.documentElement.style.fontSize='${font}px'`)
    await delay(100)
    const layout=await evaluate(`(()=>{const tile=document.querySelector('.video-link-card');const r=tile.getBoundingClientRect();const parts=[...tile.querySelectorAll('.video-link-body,.video-link-plot,.video-link-plot svg,.video-link-classes')].map(e=>{const b=e.getBoundingClientRect();return {class:e.className.baseVal??e.className,left:b.left,right:b.right,width:b.width,height:b.height,overflowRight:b.right-r.right,scrollOverflow:e.scrollWidth-e.clientWidth,verticalOverflow:e.scrollHeight-e.clientHeight}});const next=tile.nextElementSibling.getBoundingClientRect();return {tile:{left:r.left,right:r.right,width:r.width},next:{left:next.left,right:next.right,top:next.top},parts,overlap:parts.some(p=>p.right>next.left&&next.top<r.bottom)}})()`)
    report.cases.push({font,width,...layout})
    if((width===1280 && font===20)||(width===1920 && font===16))await screenshot('layout-'+width+'-font-'+font)
  }
  report.maxOverflow=Math.max(...report.cases.flatMap(c=>c.parts.map(p=>p.overflowRight)))
  report.passed=report.cases.every(c=>c.parts.every(p=>p.overflowRight<1&&p.scrollOverflow<2&&p.verticalOverflow<2))
  console.log(JSON.stringify({maxOverflow:report.maxOverflow,passed:report.passed,failures:report.cases.filter(c=>c.parts.some(p=>p.overflowRight>=1)).map(c=>({width:c.width,font:c.font,tileWidth:c.tile.width,parts:c.parts}))},null,2))
}
main().catch(e=>{report.passed=false;report.error=e.stack;process.exitCode=1}).finally(()=>{fs.writeFileSync(out+'/result.json',JSON.stringify(report,null,2));cdp?.close();chrome.kill('SIGTERM')})
