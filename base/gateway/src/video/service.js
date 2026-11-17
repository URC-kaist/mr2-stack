'use strict'

const { WebSocketServer } = require('ws')

const {
  encodeChunkMessage,
  encodeConfigMessage,
  encodeTimedChunkMessage,
} = require('./protocol')
const { createVideoStreamReceiver } = require('./receiver')
const { listBrowserStreams } = require('./stream_config')

function buffersEqual(left, right) {
  if (!left && !right) return true
  if (!left || !right) return false
  return Buffer.compare(left, right) === 0
}

function createStreamState(stream) {
  return {
    available: false,
    codec: null,
    configVersion: 0,
    encodedHeight: 0,
    encodedWidth: 0,
    latestKeyAccessUnit: null,
    keyReceivedAt: 0,
    lastDecodeTimestampUs: 0,
    pps: null,
    sps: null,
    stream,
  }
}

function createVideoGateway(options = {}) {
  const log = typeof options.log === 'function' ? options.log : () => {}
  const path = options.path || '/video-ws'
  const routeRegistry = options.routeRegistry
  const videoConfig = options.videoConfig

  if (!routeRegistry || typeof routeRegistry.register !== 'function') {
    throw new Error('createVideoGateway requires a routeRegistry')
  }
  if (!videoConfig || !Array.isArray(videoConfig.streams)) {
    throw new Error('createVideoGateway requires a loaded videoConfig')
  }

  const clientMaxBufferedBytes = Math.max(
    4096,
    Math.floor(options.clientMaxBufferedBytes || 1048576)
  )
  const WebSocketServerImpl = options.WebSocketServerImpl || WebSocketServer
  const createVideoStreamReceiverImpl =
    options.createVideoStreamReceiverImpl || createVideoStreamReceiver
  const spawnImpl = options.spawnImpl
  const now = options.now || (() => performance.now())
  const maxBufferedMs = Math.max(30, options.clientMaxBufferedMs || 150)
  const keyRequests = new Map()
  function requestKeyframe(id) {
    if (now() - (keyRequests.get(id) ?? -Infinity) < 500) return
    keyRequests.set(id, now())
    options.requestKeyframe?.(id)
  }

  const streamStates = new Map(
    videoConfig.streams.map((stream) => [stream.stream_id, createStreamState(stream)])
  )
  const subscriptionsByStream = new Map(
    videoConfig.streams.map((stream) => [stream.stream_id, new Set()])
  )
  const clients = new Set()
  let receivePaused = Boolean(options.freshness)
  const wss = new WebSocketServerImpl({ noServer: true })

  routeRegistry.register(path, wss)

  const receivers = videoConfig.streams.map((stream) =>
    createVideoStreamReceiverImpl({
      freshness: options.freshness,
      availabilityStaleMs: options.availabilityStaleMs,
      gstBinary: options.gstBinary,
      idleFlushMs: options.idleFlushMs,
      jitterLatencyMs: options.jitterLatencyMs,
      log,
      onAccessUnit: handleAccessUnit,
      onAvailabilityChange: handleAvailabilityChange,
      restartMs: options.restartMs,
      spawnImpl,
      stream,
      onPacket: options.onPacket,
    })
  )

  wss.on('connection', (ws) => {
    const client = {
      subscriptions: new Map(),
      writes: new Map(),
      ws,
    }
    clients.add(client)

    ws.on('message', (data) => {
      handleClientMessage(client, data)
    })

    ws.on('close', () => {
      removeClient(client)
    })
  })

  function handleAvailabilityChange(streamId, available) {
    const state = streamStates.get(streamId)
    if (!state) return
    state.available = available
    if (!available) {
      state.latestKeyAccessUnit = null
      state.configVersion += 1
      for (const client of subscriptionsByStream.get(streamId) || []) {
        const subscription = client.subscriptions.get(streamId)
        if (subscription) {
          subscription.needsBootstrap = true
        }
      }
    }
  }

  function updateCodecState(state, accessUnit) {
    let changed = false

    if (accessUnit.sps && !buffersEqual(state.sps, accessUnit.sps)) {
      state.sps = Buffer.from(accessUnit.sps)
      changed = true
    }

    if (accessUnit.pps && !buffersEqual(state.pps, accessUnit.pps)) {
      state.pps = Buffer.from(accessUnit.pps)
      changed = true
    }

    if (accessUnit.codec && state.codec !== accessUnit.codec) {
      state.codec = accessUnit.codec
      changed = true
    }

    if (
      Number.isInteger(accessUnit.width) &&
      accessUnit.width > 0 &&
      state.encodedWidth !== accessUnit.width
    ) {
      state.encodedWidth = accessUnit.width
      changed = true
    }

    if (
      Number.isInteger(accessUnit.height) &&
      accessUnit.height > 0 &&
      state.encodedHeight !== accessUnit.height
    ) {
      state.encodedHeight = accessUnit.height
      changed = true
    }

    if (changed && state.sps && state.pps) {
      state.latestKeyAccessUnit = null
      state.configVersion += 1
      for (const client of subscriptionsByStream.get(state.stream.stream_id) || []) {
        const subscription = client.subscriptions.get(state.stream.stream_id)
        if (subscription) {
          subscription.needsBootstrap = true
        }
      }
    }
  }

  function trySend(client, payload, streamId) {
    if (client.ws.readyState !== 1) return false
    const subscription = client.subscriptions.get(streamId)
    if (!subscription) return false
    const oldest = client.writes.values().next().value
    if (client.ws.bufferedAmount > 0 && oldest != null && now() - oldest > maxBufferedMs) {
      // Already queued TCP bytes cannot be withdrawn. Reconnect the video
      // socket to discard stale data, then resume every feed at a fresh IDR.
      client.ws.terminate()
      return false
    }
    if (client.ws.bufferedAmount + payload.length > clientMaxBufferedBytes) {
      for (const s of client.subscriptions.values()) s.needsBootstrap = true
      return false
    }
    const token = {}
    client.writes.set(token, now())
    client.ws.send(payload, (error) => {
      client.writes.delete(token)
      if (error) client.ws.terminate()
    })
    return true
  }

  function maybeSendBootstrap(client, state, subscription, keyAccessUnit) {
    if (!state.sps || !state.pps) return false
    const keyUnit = keyAccessUnit || state.latestKeyAccessUnit
    if (!keyUnit || (!keyAccessUnit && now() - state.keyReceivedAt > maxBufferedMs)) {
      requestKeyframe(state.stream.stream_id)
      return false
    }

    if (subscription.configVersionSent !== state.configVersion) {
      const configMessage = encodeConfigMessage({
        codec: state.codec || '',
        height: state.encodedHeight,
        pps: state.pps,
        sps: state.sps,
        stream_id: state.stream.stream_id,
        width: state.encodedWidth,
      })
      if (!trySend(client, configMessage, state.stream.stream_id)) {
        return false
      }
      subscription.configVersionSent = state.configVersion
    }

    const chunkMessage = encodeAccessUnitMessage(state, {
      ...keyUnit,
      key: true,
    })
    if (!trySend(client, chunkMessage, state.stream.stream_id)) {
      return false
    }

    subscription.needsBootstrap = false
    return true
  }

  function dispatchAccessUnit(state, accessUnit) {
    const subscribers = subscriptionsByStream.get(state.stream.stream_id)
    if (!subscribers || subscribers.size === 0) return

    for (const client of subscribers) {
      const subscription = client.subscriptions.get(state.stream.stream_id)
      if (!subscription) continue

      if (subscription.needsBootstrap || subscription.configVersionSent !== state.configVersion) {
        if (!accessUnit.key) {
          requestKeyframe(state.stream.stream_id)
          continue
        }
        maybeSendBootstrap(client, state, subscription, accessUnit)
        continue
      }

      trySend(client, encodeAccessUnitMessage(state, accessUnit), state.stream.stream_id)
    }
  }

  function encodeAccessUnitMessage(state, accessUnit) {
    if (!accessUnit.correlation) {
      return encodeChunkMessage({
        key: accessUnit.key,
        payload: accessUnit.payload,
        stream_id: state.stream.stream_id,
        timestamp_us: accessUnit.timestamp_us,
      })
    }
    state.lastDecodeTimestampUs = Math.max(
      state.lastDecodeTimestampUs + 1,
      Math.floor(accessUnit.timestamp_us)
    )
    return encodeTimedChunkMessage({
      key: accessUnit.key,
      payload: accessUnit.payload,
      stream_id: state.stream.stream_id,
      decode_timestamp_us: state.lastDecodeTimestampUs,
      base_access_unit_epoch_us: accessUnit.timestamp_us,
      ...accessUnit.correlation,
    })
  }

  function handleAccessUnit(streamId, accessUnit) {
    const state = streamStates.get(streamId)
    if (!state) return

    // Native AU time is from this same base host, so this only measures local
    // pipe/event-loop residence and needs no rover/laptop clock alignment.
    if (options.freshness && Date.now() * 1000 - accessUnit.timestamp_us > 150000) {
      state.latestKeyAccessUnit = null
      for (const client of subscriptionsByStream.get(streamId) || []) {
        const subscription = client.subscriptions.get(streamId)
        if (subscription) subscription.needsBootstrap = true
      }
      requestKeyframe(streamId)
      return
    }

    updateCodecState(state, accessUnit)

    if (!accessUnit.key && !accessUnit.delta) {
      return
    }

    if (accessUnit.key && state.sps && state.pps) {
      state.latestKeyAccessUnit = {
        correlation: accessUnit.correlation,
        payload: Buffer.from(accessUnit.payload),
        timestamp_us: accessUnit.timestamp_us,
      }
      state.keyReceivedAt = now()
    } else if (accessUnit.delta) {
      // Replaying an older IDR followed by current P-frames omits references.
      // A later subscriber must wait for a fresh IDR once a delta has passed.
      state.latestKeyAccessUnit = null
    }

    dispatchAccessUnit(state, accessUnit)
  }

  function subscribeClient(client, streamId) {
    const state = streamStates.get(streamId)
    if (!state) return
    if (client.subscriptions.has(streamId)) return

    const subscription = {
      configVersionSent: 0,
      needsBootstrap: true,
    }
    client.subscriptions.set(streamId, subscription)
    subscriptionsByStream.get(streamId).add(client)
    maybeSendBootstrap(client, state, subscription, null)
  }

  function unsubscribeClient(client, streamId) {
    client.subscriptions.delete(streamId)
    const subscribers = subscriptionsByStream.get(streamId)
    if (subscribers) {
      subscribers.delete(client)
    }
  }

  function removeClient(client) {
    for (const streamId of client.subscriptions.keys()) {
      unsubscribeClient(client, streamId)
    }
    clients.delete(client)
  }

  function handleClientMessage(client, data) {
    let message
    try {
      message = JSON.parse(Buffer.isBuffer(data) ? data.toString('utf8') : String(data))
    } catch {
      return
    }

    if (message.type === 'request_keyframe' && client.subscriptions.has(message.stream_id)) {
      requestKeyframe(message.stream_id)
      return
    }

    if (message.type === 'subscribe' && typeof message.stream_id === 'string') {
      subscribeClient(client, message.stream_id)
      return
    }

    if (message.type === 'unsubscribe' && typeof message.stream_id === 'string') {
      unsubscribeClient(client, message.stream_id)
    }
  }

  return {
    setReceivePolicy(policy) {
      for (const receiver of receivers) receiver.setReceivePolicy?.(policy)
      if (policy.paused && !receivePaused) {
        for (const id of streamStates.keys()) handleAvailabilityChange(id, false)
      }
      receivePaused = policy.paused
    },
    getBrowserStreams() {
      return listBrowserStreams(videoConfig).map((stream) => {
        const state = streamStates.get(stream.stream_id)
        const encodedWidth = state && state.encodedWidth > 0 ? state.encodedWidth : null
        const encodedHeight = state && state.encodedHeight > 0 ? state.encodedHeight : null
        return {
          ...stream,
          width: stream.width || encodedWidth,
          height: stream.height || encodedHeight,
          encoded_width: encodedWidth,
          encoded_height: encodedHeight,
          available: state?.available === true,
          ...options.getControlState?.(stream.stream_id),
        }
      })
    },
    start() {
      for (const receiver of receivers) {
        receiver.start()
      }
    },
    stop() {
      routeRegistry.unregister(path)
      for (const receiver of receivers) {
        receiver.stop()
      }
      for (const client of clients) {
        client.ws.terminate()
      }
      wss.close()
    },
  }
}

module.exports = {
  createVideoGateway,
}
