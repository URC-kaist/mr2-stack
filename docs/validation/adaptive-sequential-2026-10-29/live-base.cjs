const fs = require('node:fs');
const assert = require('node:assert/strict');
const { spawn, execFileSync } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const WebSocket = require('/home/mr2/mr2-stack/base/gateway/node_modules/ws');
const out = '/tmp/mr2-adaptive-live'; fs.mkdirSync(out, {recursive:true});
const ids = ['rgbd_camera','front_left_cam','front_right_cam','left_cam','right_cam'];
const labels = {'rgbd_camera':'RGB-D Cam','front_left_cam':'Front Left','front_right_cam':'Front Right','left_cam':'Left','right_cam':'Right'};
const report = {phases:[], errors:[]}; let chrome, cdp, seq=0; const pending = new Map();
function request(method,params={}) {return new Promise((resolve,reject)=>{const id=++seq; pending.set(id,{resolve,reject});cdp.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression) {const r=await request('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true}); if(r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value;}
async function states() {return (await (await fetch('http://127.0.0.1:8081/video/streams')).json()).streams;}
async function set(id,priority,enabled=true) {
  const ws=new WebSocket('ws://127.0.0.1:8081/xbee-ws');
  try {await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(Error('XBee command timeout '+id)),9000);
    ws.on('error',reject); ws.on('open',()=>ws.send(JSON.stringify({type:'video_set',stream_id:id,enabled,priority})));
    ws.on('message',buf=>{const m=JSON.parse(buf);if(m.type==='video_command_result'&&m.stream_id===id){clearTimeout(timeout);m.success?resolve():reject(Error(JSON.stringify(m)));}});
  });} finally {ws.close();}
}
async function select(high=[],disabled=[]) {for(const id of ids) await set(id,high.includes(id)?'high':'low',!disabled.includes(id));}
function cap(kbps) {const r=execFileSync('/usr/bin/python3',['/tmp/mr2-adaptive-restart-gateway.py',String(kbps)],{encoding:'utf8',timeout:25000}); console.log(r.trim());}
async function phase(name,expected,settle=10000,sample=8000) {
  console.log('PHASE '+name); await delay(settle);
  const s=(await states()).filter(s=>ids.includes(s.stream_id));
  const actual=s.filter(s=>s.enabled&&s.bitrate_kbps>0).map(s=>s.stream_id).sort();
  assert.deepEqual(actual,[...expected].sort(),name+' funded feeds: '+JSON.stringify(s));
  await evaluate('window.__draws={}'); const start=Date.now(); await delay(sample);
  const draws=await evaluate('window.__draws');
  const p={name,elapsed_s:(Date.now()-start)/1000,states:s,rendered:draws,metrics:await evaluate('window.__metrics.slice(-3)'),headers:await evaluate("[...document.querySelectorAll('.video-feed-item')].map(c=>({label:c.querySelector('.video-feed-label')?.textContent.trim(),quality:c.querySelector('.video-feed-quality')?.textContent.trim()}))")};
  report.phases.push(p); fs.writeFileSync(out+'/result.json',JSON.stringify(report,null,2));
  for(const id of expected) assert((draws[labels[id]]?.count||0)>=3,name+' no browser decode for '+id+': '+JSON.stringify(draws));
  for(const id of ids.filter(id=>!expected.includes(id))) assert((draws[labels[id]]?.count||0)===0,name+' unfunded feed decoded '+id);
  const shot=await request('Page.captureScreenshot',{format:'png'});fs.writeFileSync(out+'/'+name+'.png',Buffer.from(shot.data,'base64'));
  console.log(JSON.stringify({name,funded:actual,rendered:draws}));
}
async function main() {
 chrome=spawn('/snap/bin/chromium',['--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--ignore-certificate-errors','--remote-debugging-port=9375','--user-data-dir='+out+'/chrome','https://127.0.0.1/'],{stdio:['ignore','ignore',fs.openSync(out+'/chromium.log','w')]});
 for(let i=0;i<70;i++){try{const pages=await(await fetch('http://127.0.0.1:9375/json')).json();const page=pages.find(p=>p.type==='page');if(page){cdp=new WebSocket(page.webSocketDebuggerUrl);break;}}catch{}await delay(200);}
 assert(cdp);await new Promise((r,j)=>{cdp.on('open',r);cdp.on('error',j)});
 cdp.on('message',buf=>{const m=JSON.parse(buf);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}if(m.method==='Runtime.exceptionThrown') report.errors.push(m.params.exceptionDetails.text);});
 await request('Runtime.enable');await request('Page.enable');
 await request('Emulation.setDeviceMetricsOverride',{width:1600,height:1100,deviceScaleFactor:1,mobile:false});
 await request('Page.addScriptToEvaluateOnNewDocument',{source:`
 window.__draws={}; window.__metrics=[];
 const draw=CanvasRenderingContext2D.prototype.drawImage;
 CanvasRenderingContext2D.prototype.drawImage=function(...args){const f=args[0];if(typeof VideoFrame!=='undefined'&&f instanceof VideoFrame){const label=this.canvas.closest('.video-feed-item')?.querySelector('.video-feed-label')?.textContent.trim();if(label){const r=window.__draws[label]||{count:0,sizes:[]};r.count++;const s=f.displayWidth+'x'+f.displayHeight;if(!r.sizes.includes(s))r.sizes.push(s);window.__draws[label]=r;}}return draw.apply(this,args);};
 const NativeSocket=window.WebSocket;window.WebSocket=class extends NativeSocket{constructor(...args){super(...args);this.addEventListener('message',e=>{if(typeof e.data!=='string')return;try{const m=JSON.parse(e.data);if(m.type==='video_link_metrics'){window.__metrics.push(m);if(window.__metrics.length>20)window.__metrics.shift();}}catch{}});}};
 `});
 await request('Page.reload',{ignoreCache:true});await delay(2000);
 await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Live Feed').click()");
 await phase('normal-initial',ids,8000,10000);
 cap(32);await select([]);await phase('32-all-low',['rgbd_camera'],13000,10000);
 cap(600);await select([]);await phase('600-all-low',['rgbd_camera','front_left_cam']);
 await set('front_right_cam','high');await set('front_left_cam','high');
 await phase('600-two-high-json-order',['rgbd_camera','front_left_cam']);
 cap(1500);await select(['front_left_cam']);
 await phase('1500-one-high',['rgbd_camera','front_left_cam']);
 cap(0);await select(['front_left_cam','front_right_cam']);
 await phase('recovered-normal',ids,35000,10000);
 await set('rgbd_camera','low',false);
 await phase('rgbd-disabled',ids.filter(id=>id!=='rgbd_camera'),8000,6000);
 await set('rgbd_camera','low',true);
 await phase('rgbd-reenabled',ids,18000,10000);
 assert.deepEqual(report.errors,[]);report.passed=true;
}
main().catch(e=>{report.passed=false;report.error=e.stack;process.exitCode=1;}).finally(async()=>{
 try {cap(0);await select(['front_left_cam','front_right_cam']);report.restored=true;}catch(e){report.restore_error=e.stack;process.exitCode=1;}
 fs.writeFileSync(out+'/result.json',JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,phases:report.phases.map(p=>p.name),error:report.error,restored:report.restored,restore_error:report.restore_error},null,2));cdp?.close();chrome?.kill('SIGTERM');
});
