const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms))
function bytes(text){return Uint8Array.from(atob(text),x=>x.charCodeAt(0))}
function nals(b){const starts=[];for(let i=0;i<b.length-2;i++){if(b[i]===0&&b[i+1]===0&&(b[i+2]===1||(b[i+2]===0&&b[i+3]===1))){const len=b[i+2]===1?3:4;starts.push([i,len]);i+=len-1}}return starts.map(([p,len],i)=>b.subarray(p+len,starts[i+1]?.[0]??b.length)).filter(n=>n.length)}
function avcc(b){const ns=nals(b),a=new Uint8Array(ns.reduce((n,x)=>n+4+x.length,0)),v=new DataView(a.buffer);let at=0;for(const n of ns){v.setUint32(at,n.length);at+=4;a.set(n,at);at+=n.length}return a}
function desc(f){const s=bytes(f.sps),p=bytes(f.pps),a=new Uint8Array(11+s.length+p.length);a.set([1,s[1],s[2],s[3],255,225,s.length>>8,s.length&255]);a.set(s,8);let i=8+s.length;a[i++]=1;a[i++]=p.length>>8;a[i++]=p.length&255;a.set(p,i);return a}
function statistics(values){const v=[...values].sort((a,b)=>a-b);return v.length?{n:v.length,p50:v[Math.floor((v.length-1)*.5)],p95:v[Math.ceil(v.length*.95)-1],max:v.at(-1)}:null}
window.runBench=async function(spec){
 const fixture=await(await fetch('/fixtures/'+(spec.fixture||'poc'+spec.poc)+'.json')).json()
 const config={codec:fixture.codec,codedWidth:fixture.width,codedHeight:fixture.height,description:desc(fixture),hardwareAcceleration:'no-preference',optimizeForLatency:spec.optimize}
 const supported=await VideoDecoder.isConfigSupported(config)
 if(!supported.supported)return {spec,supported:false}
 const frames=fixture.frames.map((f,i)=>({...f,index:i,data:avcc(bytes(f.payload))}))
 const indexes=new Map(frames.map(f=>[f.timestamp_us,f.index])), sent=new Map(), rows=[], errors=[], contexts=[]
 document.body.replaceChildren()
 let fed=-1,flushing=false,maxQueue=0
 for(let n=0;n<spec.decoders;n++){
  const canvas=document.createElement('canvas');canvas.width=fixture.width;canvas.height=fixture.height;document.body.appendChild(canvas)
  const ctx=canvas.getContext('2d')
  const state={firstOutputAtFedIndex:null,outputs:0}
  const decoder=new VideoDecoder({output:frame=>{
   const now=performance.now(),index=indexes.get(frame.timestamp),t=sent.get(frame.timestamp)
   state.outputs++;if(state.firstOutputAtFedIndex==null)state.firstOutputAtFedIndex=fed
   const drawStart=performance.now();ctx.fillStyle='#060910';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(frame,0,0,canvas.width,canvas.height);const draw=performance.now()-drawStart
   rows.push({decoder:n,index,latency_ms:now-t,fed_ahead:fed-index,draw_ms:draw,flushing});frame.close()
  },error:e=>errors.push(e.message)})
  decoder.configure(config);contexts.push({decoder,state})
 }
 try{
  const start=performance.now(),inputTimes=[]
  for(let i=0;i<frames.length;i++){
   fed=i;const f=frames[i],t=performance.now();inputTimes.push(t);sent.set(f.timestamp_us,t)
   for(const {decoder} of contexts){decoder.decode(new EncodedVideoChunk({type:f.key?'key':'delta',timestamp:f.timestamp_us,data:f.data}));maxQueue=Math.max(maxQueue,decoder.decodeQueueSize)}
   await wait(Math.max(0,start+(i+1)*1000/fixture.framerate-performance.now()))
  }
  await wait(200)
  const beforeFlush=contexts.map(c=>c.state.outputs)
  flushing=true;await Promise.all(contexts.map(c=>c.decoder.flush()))
  const steady=rows.filter(r=>r.index>=12&&r.index<frames.length-5&&!r.flushing)
  return {spec,supported:true,recognizedConfig:JSON.parse(JSON.stringify(supported.config,(k,v)=>k==='description'?'avcC':v)),frames:frames.length,decodedBeforeFlush:beforeFlush,
   firstOutputAtFedIndex:contexts.map(c=>c.state.firstOutputAtFedIndex),maxDecodeQueueSize:maxQueue,
   decode_to_output_ms:statistics(steady.map(r=>r.latency_ms)),fed_ahead:statistics(steady.map(r=>r.fed_ahead)),
   draw_ms:statistics(steady.map(r=>r.draw_ms)),input_spacing_ms:statistics(inputTimes.slice(1).map((x,i)=>x-inputTimes[i])),errors,rows}
 }finally{for(const {decoder} of contexts)if(decoder.state!=='closed')decoder.close()}
}
