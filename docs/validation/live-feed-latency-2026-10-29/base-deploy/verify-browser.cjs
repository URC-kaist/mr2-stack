const assert=require('node:assert/strict'),fs=require('node:fs'),{spawn}=require('node:child_process')
const {setTimeout:wait}=require('node:timers/promises')
const WebSocket=require('/home/mr2/mr2-stack/base/gateway/node_modules/ws')
const out='/tmp/mr2-latency-base-deploy'
let chrome,cdp,sequence=0;const pending=new Map(),report={url:'https://192.168.0.55/',layouts:[]},errors=[]
function call(method,params={}){return new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});cdp.send(JSON.stringify({id,method,params}))})}
async function evaluate(expression){const value=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(value.exceptionDetails)throw Error(JSON.stringify(value.exceptionDetails));return value.result.value}
async function main(){
 chrome=spawn('/home/mr2/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux-arm64/chrome-headless-shell',['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--ignore-certificate-errors','--remote-debugging-address=127.0.0.1','--remote-debugging-port=18493','--user-data-dir='+out+'/profile','about:blank'],{stdio:['ignore','ignore',fs.openSync(out+'/chromium.log','w')]})
 for(let i=0;i<60;i++){try{const pages=await(await fetch('http://127.0.0.1:18493/json')).json();const page=pages.find(p=>p.type==='page');if(page){cdp=new WebSocket(page.webSocketDebuggerUrl);break}}catch{}await wait(100)}
 assert(cdp,'Chromium unavailable');await new Promise((r,j)=>{cdp.on('open',r);cdp.on('error',j)})
 cdp.on('message',raw=>{const message=JSON.parse(raw);if(message.id){const p=pending.get(message.id);pending.delete(message.id);message.error?p.reject(Error(JSON.stringify(message.error))):p.resolve(message.result)}else if(message.method==='Runtime.exceptionThrown')errors.push(message.params)})
 await call('Page.enable');await call('Runtime.enable');await call('Network.enable');await call('Network.setCacheDisabled',{cacheDisabled:true})
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false})
 await call('Page.navigate',{url:report.url});await wait(2000)
 await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Live Feed').click()`);await wait(1500)
 report.asset=await evaluate(`document.querySelector('script[type="module"]').getAttribute('src')`)
 assert.equal(report.asset,'/assets/index-DyvzSrJk.js')
 report.labels=await evaluate(`[...document.querySelectorAll('.video-link-latency dt')].map(e=>e.textContent)`)
 report.values=await evaluate(`[...document.querySelectorAll('.video-link-latency dd')].map(e=>e.textContent)`)
 assert.deepEqual(report.labels,['Rover → Base','Link P95','Extra queue'])
 for(const width of [1100,1440]){
  await call('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:false});await wait(100)
  const result=await evaluate(`(()=>{const tile=document.querySelector('.video-link-card'),row=tile.querySelector('.video-link-latency'),r=tile.getBoundingClientRect(),b=row.getBoundingClientRect();return {visible:b.width>0&&b.height>0&&getComputedStyle(row).visibility!=='hidden',width:b.width,overflow:row.scrollWidth-row.clientWidth,contained:b.left>=r.left&&b.right<=r.right&&b.bottom<=r.bottom}})()`)
  assert(result.visible&&result.contained&&result.overflow<2,JSON.stringify(result));report.layouts.push({width,...result})
 }
 const screenshot=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(out+'/base-live-feed.png',Buffer.from(screenshot.data,'base64'))
 assert.equal(errors.length,0,JSON.stringify(errors));report.passed=true;console.log(JSON.stringify(report))
}
main().catch(e=>{report.passed=false;report.error=e.stack;console.error(e);process.exitCode=1}).finally(()=>{report.runtimeErrors=errors;fs.writeFileSync(out+'/browser-result.json',JSON.stringify(report,null,2));cdp?.close();chrome?.kill('SIGTERM')})
