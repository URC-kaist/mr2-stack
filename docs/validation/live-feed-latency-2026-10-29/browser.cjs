const http = require('node:http'), fs = require('node:fs'), path = require('node:path')
const {spawn} = require('node:child_process')
const {setTimeout: wait} = require('node:timers/promises')
const assert = require('node:assert/strict')
const WebSocket = require('/home/mr2/mr2-stack/base/gateway/node_modules/ws')
const out = '/tmp/mr2-live-latency', root = '/home/mr2/mr2-stack/dashboard/dist'
const mime = {'.html':'text/html','.js':'application/javascript','.css':'text/css','.woff':'font/woff','.woff2':'font/woff2','.png':'image/png','.svg':'image/svg+xml'}
let clocksReady = true, traffic = true, paused = false, cdp, chrome, seq=0
const pending = new Map(), errors = [], report = {cases: []}
const clock = (role) => ({schema_version:1,role,available:true,synchronized:true,
  policy_available:true,policy_locked:clocksReady,base_locked:true,relative_ready:true,
  base_peer:'192.168.1.101',selected_source:'192.168.1.101',utc_state:'held',
  root_delay_s:.001,root_dispersion_s:.0001,system_time_offset_s:.0001,
  sampled_at_epoch_ms:Date.now()})
const server = http.createServer((req,res)=>{
  const name = new URL(req.url,'http://localhost').pathname
  res.setHeader('Cache-Control','no-store')
  if(name==='/latency/clock-status'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(clock('base')));return}
  if(name==='/video/streams'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({streams:[{stream_id:'rgbd_cam',width:640,height:360,framerate:30,available:false,state:'ON',enabled:true,priority:'low',display:{label:'RGBD',order:0}}]}));return}
  if(name==='/latency/time'){const t=Date.now()*1000;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({server_receive_epoch_us:t,server_send_epoch_us:t}));return}
  let file = path.join(root,name==='/'?'index.html':name)
  if(!file.startsWith(root+'/')||!fs.existsSync(file)||fs.statSync(file).isDirectory()){res.statusCode=404;res.end();return}
  res.setHeader('Content-Type',mime[path.extname(file)]||'application/octet-stream');fs.createReadStream(file).pipe(res)
})
const wss = new WebSocket.WebSocketServer({server})
wss.on('connection',(ws,req)=>{
  const timer=setInterval(()=>{
    if(ws.readyState!==1)return
    if(req.url==='/xbee-ws'&&!paused){
      ws.send(JSON.stringify({type:'link_status',connected:true,last_rx_ms:Date.now(),last_tx_ms:Date.now()}))
      ws.send(JSON.stringify({type:'video_link_metrics',updated_at_ms:Date.now(),budget_kbps:3000,receive_kbps:traffic?1800:0,loss_pct:0,trend_ms_s:0,
        network_delay_ms:traffic?24.6:null,network_delay_p95_ms:traffic?50.1:null,network_delay_samples:traffic?200:0,queue_delay_ms:traffic?3.5:null,
        classes:{high:{budget_kbps:2400,receive_kbps:1500,utilization_pct:62.5,streams:2},low:{budget_kbps:600,receive_kbps:300,utilization_pct:50,streams:1}}}))
    }
    if(req.url==='/rosbridge-ws')ws.send(JSON.stringify({op:'publish',topic:'/system_status/clock',msg:{status:[{level:0,message:'ready',values:Object.entries(clock('rover')).map(([key,value])=>({key,value:String(value)}))}]}}))
  },250)
  ws.on('close',()=>clearInterval(timer))
})
function call(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});cdp.send(JSON.stringify({id,method,params}))})}
async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function display(){return evaluate(`[...document.querySelectorAll('.video-link-latency dd')].map(e=>e.textContent)`)}
async function shot(name){const r=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(out+'/'+name+'.png',Buffer.from(r.data,'base64'))}
async function main(){
  await new Promise(r=>server.listen(18765,'127.0.0.1',r))
  chrome=spawn('/home/mr2/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux-arm64/chrome-headless-shell',['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--remote-debugging-address=127.0.0.1','--remote-debugging-port=18491','--user-data-dir='+out+'/profile','about:blank'],{stdio:['ignore','ignore',fs.openSync(out+'/chromium.log','w')]})
  for(let i=0;i<50;i++){try{const pages=await(await fetch('http://127.0.0.1:18491/json')).json();const page=pages.find(p=>p.type==='page');if(page){cdp=new WebSocket(page.webSocketDebuggerUrl);break}}catch{}await wait(100)}
  assert(cdp,'Chromium unavailable');await new Promise((r,j)=>{cdp.on('open',r);cdp.on('error',j)})
  cdp.on('message',raw=>{const m=JSON.parse(raw);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params)})
  await call('Page.enable');await call('Runtime.enable');await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false})
  await call('Page.navigate',{url:'http://127.0.0.1:18765/'});await wait(2000)
  await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Live Feed').click()`);await wait(2500)
  assert.deepEqual(await display(),['24.6 ms','50.1 ms','3.5 ms']);await shot('synced')
  for(const font of [16,20])for(const width of [1100,1280,1440,1920]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:false})
    await evaluate(`document.documentElement.style.fontSize='${font}px'`);await wait(80)
    const layout=await evaluate(`(()=>{const tile=document.querySelector('.video-link-card'),r=tile.getBoundingClientRect(),next=tile.nextElementSibling.getBoundingClientRect();return {tileWidth:r.width,parts:[...tile.querySelectorAll('.video-link-body,.video-link-latency,.video-link-classes')].map(e=>{const b=e.getBoundingClientRect();return {right:b.right-r.right,bottom:b.bottom-r.bottom,overflow:e.scrollWidth-e.clientWidth}}),overlap:Math.min(r.right,next.right)>Math.max(r.left,next.left)+1&&Math.min(r.bottom,next.bottom)>Math.max(r.top,next.top)+1}})()`)
    assert(!layout.overlap&&layout.parts.every(p=>p.right<1&&p.bottom<1&&p.overflow<2),JSON.stringify({font,width,layout}))
    report.cases.push({font,width,...layout})
  }
  clocksReady=false;await wait(2600);assert.deepEqual(await display(),['Clock sync','Clock sync','3.5 ms']);await shot('clock-sync')
  clocksReady=true;await wait(2600);assert.deepEqual(await display(),['24.6 ms','50.1 ms','3.5 ms'])
  traffic=false;await wait(1400);assert.deepEqual(await display(),['—','—','—'])
  traffic=true;await wait(1400);assert.deepEqual(await display(),['24.6 ms','50.1 ms','3.5 ms'])
  paused=true;await wait(4000);assert.deepEqual(await display(),['—','—','—']);await shot('stale')
  assert.equal(errors.length,0,JSON.stringify(errors));report.passed=true;console.log(JSON.stringify({passed:true,layoutCases:report.cases.length,states:['ready','clock-unready','clock-recovered','idle','traffic-resumed','telemetry-stale']}))
}
main().catch(e=>{report.error=e.stack;report.passed=false;process.exitCode=1;console.error(e)}).finally(()=>{report.errors=errors;fs.writeFileSync(out+'/browser-result.json',JSON.stringify(report,null,2));cdp?.close();chrome?.kill('SIGTERM');for(const ws of wss.clients)ws.terminate();wss.close();server.close()})
