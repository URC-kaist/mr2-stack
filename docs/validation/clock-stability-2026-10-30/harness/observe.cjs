const assert=require('node:assert/strict'),fs=require('node:fs'),{spawn}=require('node:child_process')
const {setTimeout:wait}=require('node:timers/promises')
const WebSocket=require('/home/mr2/mr2-stack/base/gateway/node_modules/ws')
const out='/tmp/mr2-clock-stability/live';let chrome,cdp,ws,ros,sequence=0
const pending=new Map(),metrics=[],clocks=[],samples=[],errors=[]
const report={duration_requested_s:300,metrics,clocks,samples,errors}
function call(method,params={}){return new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});cdp.send(JSON.stringify({id,method,params}))})}
async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function screenshot(name){const r=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(out+'/'+name+'.png',Buffer.from(r.data,'base64'))}
async function main(){
 ws=new WebSocket('wss://127.0.0.1/xbee-ws',{rejectUnauthorized:false})
 ws.on('message',raw=>{const m=JSON.parse(raw);if(m.type==='video_link_metrics')metrics.push({...m,observed_at:Date.now()})})
 ws.on('error',e=>errors.push('metrics: '+e.message))
 ros=new WebSocket('wss://127.0.0.1/rosbridge-ws',{rejectUnauthorized:false})
 ros.on('open',()=>ros.send(JSON.stringify({op:'subscribe',id:'two-minute-clock',topic:'/system_status/clock',type:'diagnostic_msgs/DiagnosticArray',throttle_rate:1000,queue_length:1})))
 ros.on('message',raw=>{const m=JSON.parse(raw);if(m.op==='publish'&&m.topic==='/system_status/clock')clocks.push({observed_at:Date.now(),status:m.msg})})
 ros.on('error',e=>errors.push('clock: '+e.message))
 chrome=spawn('/home/mr2/.cache/ms-playwright/chromium_headless_shell-1217/chrome-headless-shell-linux64/chrome-headless-shell',['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--ignore-certificate-errors','--remote-debugging-address=127.0.0.1','--remote-debugging-port=18501','--user-data-dir='+out+'/profile','about:blank'],{stdio:['ignore','ignore',fs.openSync(out+'/chromium.log','w')]})
 for(let i=0;i<60;i++){try{const pages=await(await fetch('http://127.0.0.1:18501/json')).json();const page=pages.find(p=>p.type==='page');if(page){cdp=new WebSocket(page.webSocketDebuggerUrl);break}}catch{}await wait(100)}
 assert(cdp);await new Promise((r,j)=>{cdp.on('open',r);cdp.on('error',j)})
 cdp.on('message',raw=>{const m=JSON.parse(raw);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params)})
 await call('Page.enable');await call('Runtime.enable');await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false})
 await call('Page.navigate',{url:'https://127.0.0.1/'})
 let ready=false
 for(let i=0;i<40;i++){
  ready=await evaluate(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='Live Feed')`)
  if(ready)break
  await wait(500)
 }
 assert(ready,'Live Feed tab not ready: '+await evaluate('document.body.innerText.slice(0,1000)'))
 await evaluate(`(()=>{window.__frameCounts={};const original=CanvasRenderingContext2D.prototype.drawImage;CanvasRenderingContext2D.prototype.drawImage=function(...args){if(typeof VideoFrame!=='undefined'&&args[0] instanceof VideoFrame){const label=this.canvas.closest('.video-feed-item')?.querySelector('.video-feed-label')?.textContent.trim();if(label)window.__frameCounts[label]=(window.__frameCounts[label]||0)+1}return original.apply(this,args)};[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Live Feed').click()})()`)
 let stable=0;const preparing=performance.now()
 while(performance.now()-preparing<30000){
  const values=await evaluate(`[...document.querySelectorAll('.video-link-latency dd')].map(e=>e.textContent)`)
  stable=values.length===4&&values[0].endsWith(' ms')&&values[1].endsWith(' ms')?stable+1:0
  if(stable>=5)break
  if(Math.floor((performance.now()-preparing)/1000)%10===0)console.log('Waiting for verified UI clock: '+JSON.stringify(values))
  await wait(1000)
 }
 report.clock_ready_at_start=stable>=5
 report.clock_warmup_s=(performance.now()-preparing)/1000
 report.started_at=new Date().toISOString();const start=performance.now()
 for(let i=0;i<300;i++){
  const state=await evaluate(`({delay:[...document.querySelectorAll('.video-link-latency dd')].map(e=>e.textContent),labels:[...document.querySelectorAll('.video-link-latency dt')].map(e=>e.textContent),reason:document.querySelector('.video-link-latency > div')?.title,feeds:[...document.querySelectorAll('.video-feed-item:not(.video-link-card)')].map(e=>({label:e.querySelector('.video-feed-label')?.textContent,quality:e.querySelector('.video-feed-quality')?.textContent})),frames:window.__frameCounts})`)
  samples.push({at_s:(performance.now()-start)/1000,...state})
  if(i%20===0){console.log(JSON.stringify(samples.at(-1)));fs.writeFileSync(out+'/partial.json',JSON.stringify({samples,metrics,clocks},null,2))}
  if(i===10)await screenshot('live-early')
  await wait(Math.max(0,start+(i+1)*1000-performance.now()))
 }
 await screenshot('live-end')
 report.layout=[]
 for(const width of [1100,1440]){
  await call('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:false})
  await wait(100)
  const layout=await evaluate(`(()=>{const tile=document.querySelector('.video-link-card'),r=tile.getBoundingClientRect(),row=tile.querySelector('.video-link-latency'),b=row.getBoundingClientRect();return {visible:b.width>0&&b.height>0,contained:b.left>=r.left&&b.right<=r.right&&b.bottom<=r.bottom,overflow:row.scrollWidth-row.clientWidth}})()`)
  assert(layout.visible&&layout.contained&&layout.overflow<2,JSON.stringify(layout));report.layout.push({width,...layout})
 }
 report.duration_observed_s=(performance.now()-start)/1000;report.ended_at=new Date().toISOString()
 report.summary={metrics:metrics.length,clock_messages:clocks.length,positive_traffic_reports:metrics.filter(m=>m.receive_kbps>0).length,
  absolute_delay_reports:metrics.filter(m=>Number.isFinite(m.network_delay_ms)).length,
  ui_numeric_samples:samples.filter(s=>s.delay[0]?.endsWith(' ms')).length,
  ui_clock_sync_samples:samples.filter(s=>s.delay[0]==='Clock sync').length,frames:samples.at(-1)?.frames,
  receive_range_kbps:metrics.length?[Math.min(...metrics.map(m=>m.receive_kbps)),Math.max(...metrics.map(m=>m.receive_kbps))]:null,
  delay_range_ms:metrics.filter(m=>Number.isFinite(m.network_delay_ms)).map(m=>m.network_delay_ms)}
 console.log(JSON.stringify(report.summary))
}
process.on('SIGTERM',()=>{chrome?.kill('SIGTERM');process.exit(143)})
main().catch(e=>{report.error=e.stack;console.error(e);process.exitCode=1}).finally(()=>{fs.writeFileSync(out+'/result.json',JSON.stringify(report,null,2));ws?.close();ros?.close();cdp?.close();chrome?.kill('SIGTERM')})
