'use strict'

const { spawn } = require('child_process')
const { PacketObservations } = require('./congestion')

const { AnnexBAccessUnitParser, FramedAccessUnitParser } = require('./h264')
const { RtpStreamParser } = require('./rtp_stream_parser')
const { accessUnitFingerprint, RtpFrameFingerprint } = require('./rtp_frame_fingerprint')

function buildGstReceiveArgs(stream, options = {}) {
  const latencyMs = Math.max(0, Math.floor(options.jitterLatencyMs || 0))
  if (options.framedAccessUnits) return ['-q', 'udpsrc', `port=${stream.udp_port}`,
    'caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000', '!',
    'rtpjitterbuffer', 'name=video_jitter', `latency=${latencyMs}`, 'drop-on-latency=true', '!',
    'rtph264depay', 'wait-for-keyframe=true', 'request-keyframe=true', '!', 'h264parse', 'disable-passthrough=true', '!',
    'video/x-h264,stream-format=byte-stream,alignment=au', '!',
    'appsink', 'name=video_output', 'sync=false', 'async=false']

  return [
    '-q',
    'udpsrc',
    `port=${stream.udp_port}`,
    'caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000',
    '!',
    'rtpjitterbuffer',
    `latency=${latencyMs}`,
    'drop-on-latency=true',
    '!',
    'tee',
    'name=rtp',
    'rtp.',
    '!',
    'queue',
    '!',
    'rtph264depay',
    '!',
    'h264parse',
    'disable-passthrough=true',
    '!',
    'video/x-h264,stream-format=byte-stream,alignment=au',
    '!',
    ...(options.framedAccessUnits ? ['appsink', 'name=video_output'] : ['fdsink', 'fd=1']),
    'sync=false',
    // Both live tee branches must enter PLAYING without waiting for preroll.
    // H.264 may need the next keyframe while the RTP branch already has data.
    'async=false',
    'rtp.',
    '!',
    'queue',
    '!',
    'rtpstreampay',
    '!',
    'fdsink',
    'fd=3',
    'sync=false',
    'async=false',
  ]
}

function createVideoStreamReceiver(options = {}) {
  const stream = options.stream
  const spawnImpl = options.spawnImpl || spawn
  const log = typeof options.log === 'function' ? options.log : () => {}
  const onAccessUnit =
    typeof options.onAccessUnit === 'function' ? options.onAccessUnit : () => {}
  const onAvailabilityChange =
    typeof options.onAvailabilityChange === 'function'
      ? options.onAvailabilityChange
      : () => {}
  const gstBinary = options.gstBinary || 'gst-launch-1.0'
  const nativeReceiver = !gstBinary.endsWith('gst-launch-1.0')
  let observations = new PacketObservations((p) => options.onPacket?.(stream.stream_id, p))
  const restartMs = Math.max(250, Math.floor(options.restartMs || 1000))
  const availabilityStaleMs = Math.max(250, Math.floor(options.availabilityStaleMs || 1000))

  let child = null
  let stopping = false
  let restartTimer = null
  let availabilityTimer = null
  const makeParser = () => nativeReceiver ? new FramedAccessUnitParser() : new AnnexBAccessUnitParser()
  let parser = makeParser()
  let rtpParser = new RtpStreamParser()
  const fingerprint = new RtpFrameFingerprint()
  let pendingAccessUnits = []
  let pendingMarkers = []
  let pairingTimer = null
  let available = false
  let receivePolicy = { paused: true, lower_bound_us: 0, anchor_at_us: 0, valid_until_us: 0 }
  function setReceivePolicy(policy) {
    receivePolicy = policy
    if (options.freshness && child?.stdin?.writable) {
      const data = Buffer.alloc(32)
      data.writeUInt32LE(0x4d523246); data.writeUInt32LE(policy.paused ? 1 : 0, 4)
      data.writeBigInt64LE(BigInt(Math.floor(policy.lower_bound_us)), 8)
      data.writeBigUInt64LE(BigInt(Math.max(0, Math.floor(policy.anchor_at_us))), 16)
      data.writeBigUInt64LE(BigInt(Math.max(0, Math.floor(policy.valid_until_us))), 24)
      child.stdin.write(data)
    }
  }

  const maximumPairingQueue = Math.max(4, Math.floor(options.maximumPairingQueue || 64))
  const pairingTimeoutMs = Math.max(25, Math.floor(options.pairingTimeoutMs || 100))

  function resetCorrelation() {
    if (pairingTimer) {
      clearTimeout(pairingTimer)
      pairingTimer = null
    }
    observations = new PacketObservations((p) => options.onPacket?.(stream.stream_id, p))
    parser = makeParser()
    rtpParser = new RtpStreamParser()
    fingerprint.reset()
    pendingAccessUnits = []
    pendingMarkers = []
  }

  function emitAccessUnit(accessUnit, marker = null) {
    touchAvailability()
    onAccessUnit(stream.stream_id, {
      ...accessUnit,
      ...(marker ? { correlation: marker } : {}),
      timestamp_us: Date.now() * 1000,
    })
  }

  function schedulePairingFlush() {
    if (pairingTimer || (pendingAccessUnits.length === 0 && pendingMarkers.length === 0)) return
    pairingTimer = setTimeout(() => {
      pairingTimer = null
      const cutoff = Date.now() - pairingTimeoutMs
      while (pendingAccessUnits[0]?.queuedAt <= cutoff) {
        emitAccessUnit(pendingAccessUnits.shift().value)
        log(`[video:${stream.stream_id}] emitted an access unit without RTP correlation`)
      }
      while (pendingMarkers[0]?.queuedAt <= cutoff) {
        pendingMarkers.shift()
        log(`[video:${stream.stream_id}] discarded an unpaired RTP marker`)
      }
      pairFrames()
    }, pairingTimeoutMs)
    pairingTimer.unref?.()
  }

  function pairFrames() {
    while (pendingAccessUnits.length > 0 && pendingMarkers.length > 0) {
      // Pipe delivery order is independent, and depayloading can discard frames
      // before the first keyframe or after packet loss. FIFO pairing silently
      // assigns another frame's RTP timestamp; require identical VCL content.
      const unitIndex = pendingAccessUnits.findIndex((unit) =>
        pendingMarkers.some((item) => item.fingerprint === unit.fingerprint))
      if (unitIndex < 0) break
      // A lost/corrupt frame must not block subsequent verified matches until
      // their metadata expires. Preserve decoding order, without timing it.
      for (let index = 0; index < unitIndex; index += 1) {
        emitAccessUnit(pendingAccessUnits.shift().value)
      }
      const index = pendingMarkers.findIndex((item) =>
        item.fingerprint === pendingAccessUnits[0].fingerprint)
      const accessUnit = pendingAccessUnits.shift().value
      const marker = pendingMarkers.splice(index, 1)[0].value
      emitAccessUnit(accessUnit, marker)
    }
    if (pendingAccessUnits.length > maximumPairingQueue) {
      pendingAccessUnits.splice(0, pendingAccessUnits.length - maximumPairingQueue)
      log(`[video:${stream.stream_id}] dropped unpaired H.264 access units`)
    }
    if (pendingMarkers.length > maximumPairingQueue) {
      pendingMarkers.splice(0, pendingMarkers.length - maximumPairingQueue)
      log(`[video:${stream.stream_id}] dropped unpaired RTP markers`)
    }
    schedulePairingFlush()
  }

  function setAvailability(nextAvailable) {
    if (available === nextAvailable) return
    available = nextAvailable
    onAvailabilityChange(stream.stream_id, nextAvailable)
  }

  function clearAvailabilityTimer() {
    if (!availabilityTimer) return
    clearTimeout(availabilityTimer)
    availabilityTimer = null
  }

  function touchAvailability() {
    setAvailability(true)
    clearAvailabilityTimer()
    availabilityTimer = setTimeout(() => {
      availabilityTimer = null
      setAvailability(false)
    }, availabilityStaleMs)
  }

  function scheduleRestart() {
    if (stopping || restartTimer) return
    restartTimer = setTimeout(() => {
      restartTimer = null
      start()
    }, restartMs)
  }

  function handleStdout(chunk) {
    let units
    try { units = parser.push(chunk) } catch (error) {
      log(`[video:${stream.stream_id}] access unit framing error: ${error.message}`)
      child?.kill('SIGTERM')
      return
    }
    for (const accessUnit of units) {
      if (!accessUnit.payload || accessUnit.payload.length === 0) continue
      if (accessUnit.native_metadata) {
        if (options.freshness && receivePolicy.paused) continue
        touchAvailability()
        onAccessUnit(stream.stream_id, { ...accessUnit, timestamp_us: accessUnit.timestamp_us || Date.now() * 1000 })
        continue
      }
      pendingAccessUnits.push({
        queuedAt: Date.now(), value: accessUnit,
        fingerprint: accessUnitFingerprint(accessUnit.payload),
      })
    }
    pairFrames()
  }

  function handleRtpStream(chunk) {
    try {
      for (const packet of rtpParser.push(chunk)) {
        const frameFingerprint = fingerprint.push(packet)
        if (!packet.marker || !frameFingerprint) continue
        pendingMarkers.push({
          queuedAt: Date.now(),
          fingerprint: frameFingerprint,
          value: {
            marker_sequence: packet.sequence,
            rtp_timestamp: packet.rtp_timestamp,
            ssrc: packet.ssrc,
          },
        })
      }
      pairFrames()
    } catch (error) {
      log(`[video:${stream.stream_id}] RTP metadata parse error: ${error.message || error}`)
      rtpParser.reset()
      fingerprint.reset()
      pendingMarkers = []
    }
  }

  function handleStderr(chunk) {
    const text = String(chunk).trim()
    if (text) {
      log(`[video:${stream.stream_id}] ${text}`)
    }
  }

  function start() {
    if (child || stopping) return

    const args = buildGstReceiveArgs(stream, {
      jitterLatencyMs: options.jitterLatencyMs,
      framedAccessUnits: nativeReceiver,
    })
    if (nativeReceiver) { args.shift(); args.splice(1, 0, 'name=video_source') }
    log(`[video:${stream.stream_id}] spawning ${gstBinary} ${args.join(' ')}`)

    resetCorrelation()
    child = spawnImpl(gstBinary, args, {
      stdio: [options.freshness && nativeReceiver ? 'pipe' : 'ignore', 'pipe', 'pipe', 'pipe', 'pipe'],
      ...(options.freshness && nativeReceiver ? { env: { ...process.env, MR2_VIDEO_FRESHNESS_FD: '0' } } : {}),
    })
    child.stdin?.on('error', () => {}) // Child exit can race a small policy write.
    setReceivePolicy(receivePolicy)
    let ended = false

    function finalizeChild(logMessage) {
      if (ended) return
      ended = true
      clearAvailabilityTimer()
      resetCorrelation()
      child = null
      setAvailability(false)
      if (logMessage) {
        log(logMessage)
      }
      scheduleRestart()
    }

    child.stdout.on('data', handleStdout)
    child.stderr.on('data', handleStderr)
    if (child.stdio?.[3]?.on) {
      child.stdio[3].on('data', handleRtpStream)
    } else {
      log(`[video:${stream.stream_id}] RTP metadata output fd=3 is unavailable`)
    }
    child.stdio?.[4]?.on('data', (chunk) => {
      try { observations.push(chunk) } catch (e) { log(`[video:${stream.stream_id}] ${e.message}`) }
    })
    child.on('error', (error) => {
      finalizeChild(
        `[video:${stream.stream_id}] receiver process error: ${error.message || String(error)}`
      )
    })
    child.on('exit', (code, signal) => {
      finalizeChild(
        `[video:${stream.stream_id}] receiver exited code=${code ?? 'null'} signal=${
          signal || 'null'
        }`
      )
    })
  }

  function stop() {
    stopping = true
    clearAvailabilityTimer()
    resetCorrelation()
    if (restartTimer) {
      clearTimeout(restartTimer)
      restartTimer = null
    }
    if (!child) {
      setAvailability(false)
      return
    }
    child.kill('SIGTERM')
    child = null
    setAvailability(false)
  }

  return {
    start,
    stop,
    setReceivePolicy,
  }
}

module.exports = {
  buildGstReceiveArgs,
  createVideoStreamReceiver,
}
