// Test-only composition of the production base controller and native receiver.
const { createVideoControl } = require('../../base/gateway/src/video/control')
const { createVideoStreamReceiver } = require('../../base/gateway/src/video/receiver')
const { probeToken, VIDEO_FEEDBACK } = require('../../base/gateway/src/video/control_protocol')
const stream = { stream_id:'fixture', udp_port:Number(process.argv[3]), encoder:{bitrate_kbps:1800} }
const log = value => process.stdout.write(JSON.stringify(value)+'\n')
let control, sequence=0, paused=null
const receiver = createVideoStreamReceiver({ stream, gstBinary:process.argv[2], freshness:true,
  onPacket:(id,p)=>control.observe(id,p),
  onAccessUnit:(_id,u)=>log({type:'frame', at_us:Number(process.hrtime.bigint()/1000n),
    correlation:u.correlation, payload:u.payload.toString('base64')}) })
control = createVideoControl({ streams:[stream], session:445566, freshness:true, nextSeq:()=>sequence++&255,
  writeFrame:frame=>{
    if(frame[1]===VIDEO_FEEDBACK) {
      const id=frame.length>=36?frame.readUInt16LE(26):0
      log({type:'feedback',wire_kbps:frame.readUInt32LE(12),
        probe_id:id,probe_token:probeToken(445566,id)})
    }
    return true
  },onReceivePolicy:policy=>{
    receiver.setReceivePolicy(policy)
    if(paused!==policy.paused){paused=policy.paused;log({type:'policy',paused,at_ms:Number(process.hrtime.bigint())/1e6})}
  } })
receiver.start()
const state=Buffer.alloc(26);state.writeUInt32LE(1);state.writeUInt16LE(stream.udp_port,12);state[14]=1;state[15]=3
control.handleState(state);control.start()
function stop(){control.stop();receiver.stop();setTimeout(()=>process.exit(),50)}
process.on('SIGTERM',stop);process.on('SIGINT',stop)
