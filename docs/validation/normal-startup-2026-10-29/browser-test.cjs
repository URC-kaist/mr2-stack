const assert = require('node:assert/strict')
const {spawn} = require('node:child_process')
const {setTimeout:delay} = require('node:timers/promises')
const WebSocket = require('/home/mr2/mr2-stack/base/gateway/node_modules/ws')
const fs = require('node:fs')
const chrome=spawn('/snap/bin/chromium',['--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--ignore-certificate-errors','--allow-insecure-localhost','--remote-debugging-port=9333','--user-data-dir=/tmp/mr2-normal-chromium','https://127.0.0.1/'],{stdio:['ignore','ignore',fs.openSync('/tmp/mr2-normal-live/chromium.log','w')]})
let cdp, id=0;const callbacks=new Map()
async function request(method,params={}) {
 return new Promise((resolve,reject)=>{const i=++id;callbacks.set(i,{resolve,reject});cdp.send(JSON.stringify({id:i,method,params}))})
}
async function evaluate(expression) {
 const r=await request('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true})
 if(r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails))
 return r.result.value
}
const report={}
async function streams(){return (await (await fetch('http://127.0.0.1:8081/video/streams')).json()).streams}
async function waitState(stream,state){for(let i=0;i<40;i++){const s=(await streams()).find(s=>s.stream_id===stream);if(s.state===state)return s;await delay(200)}throw Error('state timeout '+stream+' '+state)}
async function command(stream,enabled,priority){
 const ws=new WebSocket('ws://127.0.0.1:8081/xbee-ws');const started=Date.now()
 return new Promise((resolve,reject)=>{
  const timeout=setTimeout(()=>{ws.terminate();reject(Error('XBee command timeout'))},6000)
  ws.on('open',()=>ws.send(JSON.stringify({type:'video_set',stream_id:stream,enabled,priority})))
  ws.on('message',data=>{const m=JSON.parse(data);if(m.type==='video_command_result'&&m.stream_id===stream){clearTimeout(timeout);ws.close();m.elapsed_ms=Date.now()-started;if(m.success)resolve(m);else reject(Error(m.message))}})
  ws.on('error',reject)
 })
}
async function main(){
 for(let i=0;i<80;i++){try{const pages=await (await fetch('http://127.0.0.1:9333/json')).json();const page=pages.find(p=>p.type==='page');if(page){cdp=new WebSocket(page.webSocketDebuggerUrl);break}}catch{}await delay(200)}
 assert(cdp,'Chromium CDP unavailable')
 await new Promise((r,j)=>{cdp.on('open',r);cdp.on('error',j)})
 cdp.on('message',b=>{const m=JSON.parse(b);if(m.id){const c=callbacks.get(m.id);callbacks.delete(m.id);m.error?c.reject(Error(JSON.stringify(m.error))):c.resolve(m.result)}})
 await request('Runtime.enable');await request('Emulation.setDeviceMetricsOverride',{width:1440,height:1080,deviceScaleFactor:1,mobile:false});await delay(1500)
 report.initialPage=await evaluate('({title:document.title,buttons:[...document.querySelectorAll("button")].map(x=>x.textContent.trim())})')
 await evaluate(`(()=>{window.__videoFrames={};const draw=CanvasRenderingContext2D.prototype.drawImage;CanvasRenderingContext2D.prototype.drawImage=function(...args){const frame=args[0];if(typeof VideoFrame!=='undefined'&&frame instanceof VideoFrame){const label=this.canvas.closest('.video-feed-item')?.querySelector('.video-feed-label')?.textContent.trim();if(label){const r=window.__videoFrames[label]||{count:0,sizes:[]};r.count++;const size=frame.displayWidth+'x'+frame.displayHeight;if(!r.sizes.includes(size))r.sizes.push(size);window.__videoFrames[label]=r;}}return draw.apply(this,args)};const tab=[...document.querySelectorAll('button')].find(x=>/live feed/i.test(x.textContent));if(tab)tab.click();return !!tab})()`)
 await delay(18000)
 report.before=await evaluate('window.__videoFrames')
 report.body=await evaluate('document.body.innerText.slice(0,6000)')
 assert(report.before['Front Left']?.count>=20,'Front Left browser decode missing: '+JSON.stringify(report))
 assert(report.before['RGB-D Cam']?.count>=20,'RGB-D browser decode missing: '+JSON.stringify(report))
 let rows=await streams();const front=rows.find(x=>x.stream_id==='front_left_cam'),rgbd=rows.find(x=>x.stream_id==='rgbd_camera')
 assert.equal(front.state,'ON');assert.equal(rgbd.state,'ON')
 assert.equal(rows.find(x=>x.stream_id==='front_right_cam').state,'NO DEVICE')
 report.allocationBefore={front,rgbd}
 // Exercise the actual dashboard button, not a ROS service or direct serial call.
 await evaluate(`(()=>{const card=[...document.querySelectorAll('.video-feed-item')].find(x=>x.querySelector('.video-feed-label')?.textContent.trim()==='Front Left');const b=card.querySelector('button');if(b.textContent.trim()!=='Disable')throw Error(b.textContent);b.click()})()`)
 report.off=await waitState('front_left_cam','OFF');await delay(1200)
 const stopped=await evaluate('window.__videoFrames["Front Left"].count');await delay(1200)
 assert((await evaluate('window.__videoFrames["Front Left"].count'))<=stopped+1,'Frames continued while OFF')
 await evaluate(`(()=>{const card=[...document.querySelectorAll('.video-feed-item')].find(x=>x.querySelector('.video-feed-label')?.textContent.trim()==='Front Left');card.querySelector('button').click()})()`)
 await waitState('front_left_cam','ON');await delay(4000)
 assert((await evaluate('window.__videoFrames["Front Left"].count'))>stopped+10,'Frames did not resume')
 report.priorityCommands=[]
 report.priorityCommands.push(await command('rgbd_camera',true,'high'))
 report.priorityCommands.push(await command('front_left_cam',true,'low'))
 await delay(7500)
 rows=await streams();report.allocationSwapped={front:rows.find(x=>x.stream_id==='front_left_cam'),rgbd:rows.find(x=>x.stream_id==='rgbd_camera')}
 assert.equal(report.allocationSwapped.rgbd.priority,'high');assert.equal(report.allocationSwapped.front.priority,'low')
 report.after=await evaluate('window.__videoFrames')
 const area=s=>s.split('x').map(Number).reduce((a,b)=>a*b)
 const beforeFront=Math.max(...report.before['Front Left'].sizes.map(area))
 const beforeRgbd=Math.max(...report.before['RGB-D Cam'].sizes.map(area))
 report.frontSizes=report.after['Front Left'].sizes
 report.rgbdSizes=report.after['RGB-D Cam'].sizes
 const shot=await request('Page.captureScreenshot',{format:'png',captureBeyondViewport:false})
 fs.writeFileSync('/tmp/mr2-normal-live/live-feed.png',Buffer.from(shot.data,'base64'))
 report.radios=await (await fetch('http://127.0.0.1:8081/rocket-m2/status')).json()
 assert(report.radios.statuses.every(s=>s.connected),'Radio management poll failed')
 report.rosbridge=await new Promise((resolve,reject)=>{
  const ws=new WebSocket('wss://127.0.0.1/rosbridge-ws',{rejectUnauthorized:false})
  const timer=setTimeout(()=>{ws.terminate();reject(Error('ROS through nginx timeout'))},10000)
  ws.on('open',()=>ws.send(JSON.stringify({op:'subscribe',id:'normal-check',topic:'/video/state',type:'std_msgs/msg/UInt8MultiArray',throttle_rate:1000})))
  ws.on('message',b=>{const m=JSON.parse(b);if(m.op==='publish'){clearTimeout(timer);ws.close();resolve({topic:m.topic,received:true})}})
  ws.on('error',reject)
 })
 report.passed=true
}
main().catch(e=>{report.passed=false;report.error=e.stack;process.exitCode=1}).finally(async()=>{
 try{await command('front_left_cam',true,'high');await command('rgbd_camera',true,'low')}catch(e){report.restoreError=e.message}
 fs.writeFileSync('/tmp/mr2-normal-live/browser-result.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));cdp?.close();chrome.kill('SIGTERM')
})
