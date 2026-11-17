const http=require('node:http'),fs=require('node:fs'),{spawn}=require('node:child_process')
const {setTimeout:wait}=require('node:timers/promises')
const WebSocket=require('/home/mr2/mr2-stack/base/gateway/node_modules/ws')
const out='/tmp/mr2-steady-latency-review'
const lowFps=process.argv.includes('--low-fps'),reportName=lowFps?'browser-lowfps-results.json':'browser-results.json'
let browser,cdp,sequence=0,server;const pending=new Map(),result={cases:[]}
function call(method,params={}){return new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});cdp.send(JSON.stringify({id,method,params}))})}
async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function main(){
 server=http.createServer((req,res)=>{
  if(req.url==='/'){res.setHeader('content-type','text/html');res.end('<!doctype html><title>Isolated steady decoder benchmark</title><script src="/browser-bench.js"></script>');return}
  const path=req.url==='/browser-bench.js'?out+'/browser-bench.js':/^\/fixtures\/(?:poc[02]|fps5)\.json$/.test(req.url)?out+req.url:null
  if(!path||!fs.existsSync(path)){res.writeHead(404);res.end();return}res.setHeader('content-type',req.url.endsWith('.json')?'application/json':'application/javascript');res.end(fs.readFileSync(path))
 })
 await new Promise(r=>server.listen(18630,'127.0.0.1',r))
 browser=spawn('/home/mr2/.cache/ms-playwright/chromium_headless_shell-1217/chrome-headless-shell-linux64/chrome-headless-shell',['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--remote-debugging-address=127.0.0.1','--remote-debugging-port=18631','--user-data-dir='+out+'/profile','about:blank'],{stdio:['ignore','ignore',fs.openSync(out+'/chromium.log','w')]})
 for(let i=0;i<80;i++){try{const p=(await(await fetch('http://127.0.0.1:18631/json')).json()).find(x=>x.type==='page');if(p){cdp=new WebSocket(p.webSocketDebuggerUrl);break}}catch{}await wait(100)}
 if(!cdp)throw Error('No CDP')
 await new Promise((r,j)=>{cdp.on('open',r);cdp.on('error',j)})
 cdp.on('message',raw=>{const m=JSON.parse(raw);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}})
 result.browser=await call('Browser.getVersion');await call('Page.enable');await call('Runtime.enable')
 await call('Page.navigate',{url:'http://127.0.0.1:18630/'})
 for(let i=0;i<50;i++){if(await evaluate('typeof window.runBench==="function"'))break;await wait(100)}
 result.environment=await evaluate('({secureContext:isSecureContext,videoDecoder:typeof VideoDecoder,userAgent:navigator.userAgent})')
 const specs=lowFps?[{fixture:'fps5',poc:0,optimize:false,decoders:1},{fixture:'fps5',poc:0,optimize:true,decoders:1}]:[{poc:0,optimize:false,decoders:1},{poc:0,optimize:true,decoders:1},{poc:2,optimize:false,decoders:1},{poc:2,optimize:true,decoders:1},{poc:0,optimize:false,decoders:5},{poc:0,optimize:true,decoders:5},{poc:0,optimize:false,decoders:5}]
 for(const spec of specs){
  const r=await evaluate('window.runBench('+JSON.stringify(spec)+')');result.cases.push(r)
  console.log(JSON.stringify({...r,rows:undefined}));fs.writeFileSync(out+'/'+reportName,JSON.stringify(result,null,2))
 }
}
process.on('SIGTERM',()=>{browser?.kill('SIGTERM');process.exit(143)})
main().catch(e=>{result.error=e.stack;console.error(e);process.exitCode=1}).finally(()=>{fs.writeFileSync(out+'/'+reportName,JSON.stringify(result,null,2));cdp?.close();browser?.kill('SIGTERM');server?.close()})
