import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { getVideoGatewayClient, type VideoStreamInfo } from '../lib/videoGateway'
import type { VideoChunkMessage, VideoConfigMessage } from '../lib/videoProtocol'
import { latencyDiagnostics } from '../lib/latencyDiagnostics'
import { VideoStartupQueue } from '../lib/videoStartupQueue'
import { createVideoRenderer } from '../lib/videoRenderer'
import { annexBToAvcc } from '../lib/videoAvcc'
import './VideoStreamCard.css'

type VideoStreamCardProps = {
  stream: VideoStreamInfo
  videoWidth?: number
  videoHeight?: number
  onFrameRendered?: (streamId: string) => void
  onPlaybackStats?: (streamId: string, stats: VideoPlaybackStats) => void
}

export type VideoPlaybackStats = {
  width: number | null
  height: number | null
  fps: number
}

type H264PayloadFormat = 'annexb' | 'avcc'

function buildAvcDescription(config: VideoConfigMessage) {
  const sps = config.sps
  const pps = config.pps
  const output = new Uint8Array(11 + sps.length + pps.length)
  let offset = 0
  output[offset++] = 1
  output[offset++] = sps[1] ?? 0x42
  output[offset++] = sps[2] ?? 0xe0
  output[offset++] = sps[3] ?? 0x1f
  output[offset++] = 0xff
  output[offset++] = 0xe1
  output[offset++] = (sps.length >> 8) & 0xff
  output[offset++] = sps.length & 0xff
  output.set(sps, offset)
  offset += sps.length
  output[offset++] = 1
  output[offset++] = (pps.length >> 8) & 0xff
  output[offset++] = pps.length & 0xff
  output.set(pps, offset)
  return output
}

function deriveAvcCodecString(sps: Uint8Array) {
  if (sps.length < 4) return 'avc1.42E01F'
  const profile = sps[1].toString(16).padStart(2, '0').toUpperCase()
  const constraints = sps[2].toString(16).padStart(2, '0').toUpperCase()
  const level = sps[3].toString(16).padStart(2, '0').toUpperCase()
  return `avc1.${profile}${constraints}${level}`
}

function normalizeCodecString(codec: string | null | undefined, sps: Uint8Array) {
  if (!codec) return deriveAvcCodecString(sps)
  if (/^avc1\./i.test(codec)) {
    return `avc1.${codec.slice(5)}`
  }
  return deriveAvcCodecString(sps)
}

function buildDecoderCandidates(message: VideoConfigMessage) {
  const codec = normalizeCodecString(message.codec, message.sps)
  const baseConfig: VideoDecoderConfig = {
    codec,
    // Let the browser fall back to software when hardware H.264 decoding is
    // unavailable (for example, on a base station without an active GPU).
    hardwareAcceleration: 'no-preference',
    optimizeForLatency: true,
  }
  if (message.width > 0) {
    baseConfig.codedWidth = message.width
  }
  if (message.height > 0) {
    baseConfig.codedHeight = message.height
  }

  return [
    {
      config: {
        ...baseConfig,
        description: buildAvcDescription(message),
      },
      payloadFormat: 'avcc' as H264PayloadFormat,
    },
    {
      config: baseConfig,
      payloadFormat: 'annexb' as H264PayloadFormat,
    },
  ]
}

async function selectDecoderConfiguration(message: VideoConfigMessage) {
  const candidates = buildDecoderCandidates(message)
  if (typeof VideoDecoder.isConfigSupported !== 'function') {
    return candidates[0]
  }

  for (const candidate of candidates) {
    try {
      const result = await VideoDecoder.isConfigSupported(candidate.config)
      if (result.supported) {
        return candidate
      }
    } catch {
      continue
    }
  }

  // Older implementations can reject a latency hint despite supporting H.264.
  for (const candidate of candidates) {
    const config = { ...candidate.config, optimizeForLatency: false }
    try { if ((await VideoDecoder.isConfigSupported(config)).supported) return { ...candidate, config } } catch { /* try next format */ }
  }
  return null
}

const VideoStreamCard = ({
  stream,
  videoWidth,
  videoHeight,
  onFrameRendered,
  onPlaybackStats,
}: VideoStreamCardProps) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const decoderRef = useRef<VideoDecoder | null>(null)
  const decoderConfigRef = useRef<VideoDecoderConfig | null>(null)
  const payloadFormatRef = useRef<H264PayloadFormat>('avcc')
  const configGenerationRef = useRef(0)
  const waitingForKeyframeRef = useRef(true)
  const playbackRef = useRef({ frames: 0, width: 0, height: 0 })
  const webCodecsAvailable = typeof VideoDecoder !== 'undefined'
  const [status, setStatus] = useState(() =>
    webCodecsAvailable
      ? stream.available
        ? 'Waiting for codec config...'
        : 'Waiting for video ingest...'
      : 'WebCodecs is unavailable in this browser'
  )
  const [hasFrame, setHasFrame] = useState(false)
  const [frameSize, setFrameSize] = useState<{ width: number; height: number } | null>(null)
  const [canvasGeneration, setCanvasGeneration] = useState(0)
  const [workerDisabled, setWorkerDisabled] = useState(false)
  const onFrameRef = useRef(onFrameRendered)
  const lastFrameAt = useRef(0)
  useEffect(() => { onFrameRef.current = onFrameRendered }, [onFrameRendered])

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (lastFrameAt.current && performance.now() - lastFrameAt.current > 1000) {
        lastFrameAt.current = 0
        setHasFrame(false)
        setStatus('Waiting for fresh video...')
      }
    }, 250)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!onPlaybackStats) return
    playbackRef.current = { frames: 0, width: 0, height: 0 }
    let previousFrames = 0
    let previousTime = performance.now()
    const timer = window.setInterval(() => {
      const now = performance.now()
      const { frames, width, height } = playbackRef.current
      onPlaybackStats(stream.stream_id, {
        width: width || null,
        height: height || null,
        fps: (frames - previousFrames) * 1000 / Math.max(1, now - previousTime),
      })
      previousFrames = frames
      previousTime = now
    }, 1000)
    return () => window.clearInterval(timer)
  }, [onPlaybackStats, stream.stream_id])

  const videoStyle = useMemo(
    () => {
      const width = videoWidth ?? frameSize?.width
      const height = videoHeight ?? frameSize?.height
      return ({
        ...(width && height ? { '--video-aspect': `${width} / ${height}` } : {}),
      }) as CSSProperties
    },
    [frameSize, videoHeight, videoWidth]
  )

  useEffect(() => {
    let active = true
    let decoderGeneration = 0
    const startup = new VideoStartupQueue()
    let configuring = false
    let lastKeyRequest = -Infinity
    const requestKeyframe = () => {
      if (performance.now() - lastKeyRequest < 500) return
      lastKeyRequest = performance.now()
      getVideoGatewayClient().requestKeyframe(stream.stream_id)
    }

    if (!webCodecsAvailable) {
      return
    }
    const canvas = canvasRef.current
    if (!canvas) return
    const fallback = () => {
      if (!active || workerDisabled) return
      setWorkerDisabled(true)
      setCanvasGeneration((value) => value + 1)
    }
    let renderer: ReturnType<typeof createVideoRenderer>
    try {
      renderer = createVideoRenderer(canvas, stream.stream_id, (result) => {
        if (!active) return
        lastFrameAt.current = performance.now()
        playbackRef.current.frames += 1
        playbackRef.current.width = result.width
        playbackRef.current.height = result.height
        setFrameSize((old) => old?.width === result.width && old.height === result.height ? old : { width: result.width, height: result.height })
        latencyDiagnostics.observeVideoRender(stream.stream_id, result.timestampUs, result.renderEpochUs)
        setHasFrame(true); setStatus('Live')
        onFrameRef.current?.(stream.stream_id)
      }, fallback, !workerDisabled)
    } catch {
      fallback()
      return () => { active = false }
    }

    function describeError(error: unknown) {
      return error instanceof Error ? error.message : String(error)
    }

    function closeDecoder(decoder: VideoDecoder | null) {
      if (!decoder) return
      try {
        decoder.close()
      } catch {
        // Ignore repeated-close and invalid-state cleanup paths.
      }
    }

    function createDecoder() {
      decoderGeneration += 1
      const generation = decoderGeneration
      const decoder = new VideoDecoder({
        output: (frame) => {
          if (!active || generation !== decoderGeneration) {
            frame.close()
            return
          }

          waitingForKeyframeRef.current = false
          renderer.draw(frame)
        },
        error: (error) => {
          if (!active || generation !== decoderGeneration) return
          recoverDecoder(`Decoder error: ${error.message}`)
        },
      })
      decoderRef.current = decoder
      return decoder
    }

    function recoverDecoder(reason: string) {
      waitingForKeyframeRef.current = true
      requestKeyframe()
      const decoderConfig = decoderConfigRef.current
      const payloadFormat = payloadFormatRef.current
      closeDecoder(decoderRef.current)
      decoderRef.current = null

      if (!decoderConfig) {
        setStatus(reason)
        return
      }

      try {
        const recovered = createDecoder()
        recovered.configure(decoderConfig)
        payloadFormatRef.current = payloadFormat
        setStatus(`${reason}; waiting for keyframe...`)
      } catch (recoveryError) {
        decoderConfigRef.current = null
        setStatus(`Decoder error: ${describeError(recoveryError)}`)
      }
    }

    createDecoder()

    const unsubscribe = getVideoGatewayClient().subscribe(stream.stream_id, (message) => {
      if (message.kind === 'config') {
        const generation = configGenerationRef.current + 1
        configGenerationRef.current = generation
        configuring = true
        startup.clear()
        decoderConfigRef.current = null
        waitingForKeyframeRef.current = true
        setStatus('Configuring decoder...')

        void (async () => {
          const supported = await selectDecoderConfiguration(message)
          if (!active || generation !== configGenerationRef.current) return
          if (!supported) {
            configuring = false
            startup.clear()
            decoderConfigRef.current = null
            setStatus('WebCodecs does not support this H.264 stream')
            return
          }

          try {
            // Encoder restarts may reuse SPS/PPS identifiers at a new size.
            // A new decoder also fences off output from the previous config.
            closeDecoder(decoderRef.current)
            const decoder = createDecoder()
            decoder.configure(supported.config)
            decoderConfigRef.current = supported.config
            payloadFormatRef.current = supported.payloadFormat
            waitingForKeyframeRef.current = true
            setStatus('Waiting for keyframe...')
            configuring = false
            const pending = startup.take()
            for (const chunk of pending) decodeChunk(chunk)
            if (pending.length === 0) requestKeyframe()
          } catch (error) {
            decoderConfigRef.current = null
            configuring = false
            startup.clear()
            setStatus(`Decoder error: ${describeError(error)}`)
          }
        })()
        return
      }

      const chunk = message as VideoChunkMessage
      if (configuring) {
        if (!startup.push(chunk)) requestKeyframe()
        return
      }
      decodeChunk(chunk)
    })

    function decodeChunk(chunk: VideoChunkMessage) {
      const decoderConfig = decoderConfigRef.current
      const decoder = decoderRef.current
      if (!decoderConfig || !decoder) {
        return
      }
      if (waitingForKeyframeRef.current && !chunk.key) {
        requestKeyframe()
        return
      }
      if (decoder.decodeQueueSize > 3 && !chunk.key) {
        recoverDecoder('Decoder queue exceeded latency limit')
        return
      }
      if (decoder.decodeQueueSize > 5 && chunk.key) {
        try {
          decoder.reset()
          decoder.configure(decoderConfig)
        } catch (error) {
          recoverDecoder(`Decoder error: ${describeError(error)}`)
          return
        }
      }

      const payload = payloadFormatRef.current === 'annexb' ? chunk.payload : annexBToAvcc(chunk.payload)
      if (chunk.key) {
        waitingForKeyframeRef.current = false
      }
      try {
        decoder.decode(
          new EncodedVideoChunk({
            type: chunk.key ? 'key' : 'delta',
            timestamp: chunk.timestampUs,
            data: payload,
          })
        )
      } catch (error) {
        recoverDecoder(`Decoder error: ${describeError(error)}`)
      }
    }

    return () => {
      active = false
      lastFrameAt.current = 0
      unsubscribe()
      renderer.close()
      startup.clear()
      closeDecoder(decoderRef.current)
      decoderRef.current = null
      decoderConfigRef.current = null
      payloadFormatRef.current = 'avcc'
      waitingForKeyframeRef.current = true
    }
  }, [
    stream.stream_id,
    webCodecsAvailable,
    canvasGeneration,
    workerDisabled,
  ])

  return (
    <div className="video-stream-card" style={videoStyle}>
      <canvas key={canvasGeneration} ref={canvasRef} className="video-stream-canvas" style={{ visibility: hasFrame ? 'visible' : 'hidden' }} />
      <div className={`video-stream-overlay ${hasFrame ? 'video-stream-overlay--live' : ''}`}>
        {status}
      </div>
    </div>
  )
}

export default VideoStreamCard
