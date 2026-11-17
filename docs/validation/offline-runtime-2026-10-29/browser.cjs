const fs=require('node:fs'),assert=require('node:assert/strict');
const {spawn,execFileSync}=require('node:child_process');
const {setTimeout:delay}=require('node:timers/promises');
const WebSocket=require('/home/mr2/mr2-stack/base/gateway/node_modules/ws');
const out='/tmp/mr2-offline-browser';fs.mkdirSync(out,{recursive:true});
const chrome=spawn('/snap/bin/chromium',['--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--ignore-certificate-errors','--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run','--remote-debugging-port=9387','--user-data-dir='+out+'/profile','about:blank'],{stdio:['ignore','ignore',fs.openSync(out+'/chromium.log','w')]});
let cdp,seq=0;const pending=new Map(),report={localResponses:[],blocked:[],exceptions:[]};
function request(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});cdp.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const r=await request('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
async function main(){
 for(let i=0;i<60;i++){try{const pages=await(await fetch('http://127.0.0.1:9387/json')).json();const page=pages.find(p=>p.type==='page');if(page){cdp=new WebSocket(page.webSocketDebuggerUrl);break;}}catch{}await delay(200);}
 assert(cdp);await new Promise((r,j)=>{cdp.on('open',r);cdp.on('error',j);});
 cdp.on('message',data=>{const m=JSON.parse(data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}
  if(m.method==='Runtime.exceptionThrown')report.exceptions.push(m.params.exceptionDetails.text);
  if(m.method==='Network.responseReceived')report.localResponses.push({url:m.params.response.url,status:m.params.response.status,type:m.params.type});
  if(m.method==='Fetch.requestPaused'){const {requestId,request:r}=m.params;let allow=false;try{const u=new URL(r.url);allow=['127.0.0.1','localhost','[::1]'].includes(u.hostname)||['data:','blob:'].includes(u.protocol);}catch{}
   if(allow)request('Fetch.continueRequest',{requestId}).catch(()=>{});
   else{report.blocked.push(r.url);request('Fetch.failRequest',{requestId,errorReason:'BlockedByClient'}).catch(()=>{});}
  }
 });
 await request('Page.enable');await request('Runtime.enable');await request('Network.enable');
 await request('Network.setCacheDisabled',{cacheDisabled:true});await request('Network.setBypassServiceWorker',{bypass:true});
 await request('Fetch.enable',{patterns:[{urlPattern:'*'}]});
 await request('Page.navigate',{url:'https://127.0.0.1/'});await delay(3500);
 report.ui=await evaluate("({title:document.title,buttons:[...document.querySelectorAll('button')].map(b=>b.textContent.trim()),secure:window.isSecureContext,webcodecs:typeof VideoDecoder==='function'})");
 assert(report.ui.buttons.includes('Live Feed'));assert(report.ui.secure&&report.ui.webcodecs);
 await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Live Feed').click()");await delay(1000);
 report.liveFeed=await evaluate("document.querySelector('.video-feed-grid')!==null");assert(report.liveFeed);
 report.tiles=[];
 for(const name of ['kaist','pump_track','naip']){
  const file=execFileSync('find',['/var/www/mr2-tiles/'+name,'-type','f','-name','*.png','-print','-quit'],{encoding:'utf8'}).trim();
  const url='/tiles/'+file.replace('/var/www/mr2-tiles/','');
  const status=await evaluate(`fetch(${JSON.stringify(url)}).then(r=>({status:r.status,type:r.headers.get('content-type')}))`);
  report.tiles.push({name,...status});assert.equal(status.status,200);assert.match(status.type,/image\/png/);
 }
 report.externalBlocked=await evaluate("fetch('https://example.com/__mr2_offline_probe__').then(()=>false,()=>true)");assert(report.externalBlocked);
 assert(report.blocked.some(u=>u.includes('__mr2_offline_probe__')));
 assert.deepEqual(report.exceptions,[]);
 const assets=report.localResponses.filter(r=>['Document','Script','Stylesheet','Font'].includes(r.type));
 assert(assets.length>5&&assets.every(r=>r.status===200),JSON.stringify(assets));
 report.notes='Only this browser page external requests were blocked. Gateway/ROS were stopped; live camera playback was not exercised by this UI check.';
 report.passed=true;
 const shot=await request('Page.captureScreenshot',{format:'png'});fs.writeFileSync(out+'/offline-ui.png',Buffer.from(shot.data,'base64'));
}
main().catch(e=>{report.passed=false;report.error=e.stack;process.exitCode=1;}).finally(()=>{fs.writeFileSync(out+'/result.json',JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,error:report.error,ui:report.ui,tiles:report.tiles,blocked:report.blocked,exceptions:report.exceptions},null,2));cdp?.close();chrome.kill('SIGTERM');});
