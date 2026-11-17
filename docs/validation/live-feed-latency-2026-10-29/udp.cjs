const dgram=require('node:dgram'),{spawn}=require('node:child_process'),fs=require('node:fs'),assert=require('node:assert/strict')
const {setTimeout:wait}=require('node:timers/promises')
const root='/home/mr2/mr2-stack',native=root+'/rover/ros2_ws/install/mr2_video_streaming/lib/mr2_video_streaming/'
const {createVideoControl}=require(root+'/base/gateway/src/video/control')
const {createVideoStreamReceiver}=require(root+'/base/gateway/src/video/receiver')
let receiver,child,control,timer,relay,pending=new Set(),observations=0,frames=0,metrics=[],feedbackSizes=new Set(),logs=[]
async function bind(socket){await new Promise((r,j)=>{socket.once('error',j);socket.bind(0,'127.0.0.1',r)});return socket.address().port}
async function main(){
 const target=dgram.createSocket('udp4'),port=await bind(target);await new Promise(r=>target.close(r))
 relay=dgram.createSocket('udp4');const relayPort=await bind(relay)
 relay.on('message',b=>{const id=setTimeout(()=>{pending.delete(id);relay.send(b,port,'127.0.0.1')},75);pending.add(id)})
 const stream={stream_id:'latency_test',udp_port:port,encoder:{bitrate_kbps:1800}}
 control=createVideoControl({streams:[stream],initialKbps:1800,maxKbps:1800,nextSeq:()=>0,writeFrame:f=>{feedbackSizes.add(f.length);return true},broadcast:m=>{if(m.type==='video_link_metrics'&&m.network_delay_samples>10)metrics.push(m)}})
 const state=Buffer.alloc(26);state.writeUInt32LE(1,0);state.writeUInt16LE(port,12);state[14]=1;state[15]=3;state.writeUInt32LE(1530,16);control.handleState(state)
 receiver=createVideoStreamReceiver({stream,gstBinary:native+'video_receiver',onPacket:(id,p)=>{observations++;control.observe(id,p)},onAccessUnit:()=>frames++,log:m=>logs.push(m)})
 receiver.start();control.start();await wait(400)
 child=spawn(native+'video_sender',['latency_test','videotestsrc','is-live=true','num-buffers=240','name=src','!','video/x-raw,width=320,height=180,framerate=30/1','!','x264enc','name=latency_encoder','bitrate=1530','tune=zerolatency','speed-preset=ultrafast','key-int-max=30','!','h264parse','!','rtph264pay','name=latency_pay','pt=96','mtu=1176','!','udpsink','name=video_udp','host=127.0.0.1','port='+relayPort,'sync=false','async=false'],{stdio:['ignore','pipe','pipe']})
 child.stderr.on('data',b=>logs.push(String(b)))
 timer=setTimeout(()=>child.kill('SIGKILL'),15000)
 const exit=await new Promise((r,j)=>{child.on('exit',r);child.on('error',j)});assert.equal(exit,0,logs.join('\n'));clearTimeout(timer);await wait(500)
 assert(observations>200&&frames>150,{observations,frames})
 assert(metrics.length>=5,metrics)
 for(const m of metrics){assert(m.network_delay_ms>=70&&m.network_delay_ms<200,m);assert(m.network_delay_p95_ms>=m.network_delay_ms&&m.network_delay_p95_ms<250,m)}
 assert.deepEqual([...feedbackSizes],[28])
 const report={passed:true,injected_udp_delay_ms:75,observations,decoded_access_units:frames,metrics:metrics.map(({network_delay_ms,network_delay_p95_ms,queue_delay_ms,network_delay_samples})=>({network_delay_ms,network_delay_p95_ms,queue_delay_ms,network_delay_samples}))}
 fs.writeFileSync('/tmp/mr2-live-latency/udp-result.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report))
}
main().catch(e=>{console.error(e);process.exitCode=1}).finally(()=>{clearTimeout(timer);child?.kill('SIGTERM');receiver?.stop();control?.stop();for(const t of pending)clearTimeout(t);relay?.close();fs.writeFileSync('/tmp/mr2-live-latency/udp-native.log',logs.join('\n'))})
