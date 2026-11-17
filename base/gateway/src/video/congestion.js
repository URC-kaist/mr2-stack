'use strict'
const { probeToken } = require('./control_protocol')

// Receiver-side delay-based GCC: packet groups -> accumulated inter-arrival
// delay -> trendline/overuse -> AIMD. This is not the full libwebrtc GoogCC.
class DelayController {
  constructor({ initialKbps = 3000, maxKbps = 30000, minKbps = 32, now = () => Number(process.hrtime.bigint()) / 1e6, probeSession = 0 } = {}) {
    this.now = now
    this.probeSession = probeSession
    this.initial = Math.min(initialKbps, maxKbps)
    this.budget = this.initial
    this.max = maxKbps
    this.min = Math.min(minKbps, maxKbps)
    this.group = null
    this.previous = null
    this.points = []
    this.accumulated = 0
    this.smoothed = 0
    this.trend = 0
    this.lastPacket = null
    this.lastUpdate = now()
    this.lastDecrease = -Infinity
    this.overuseSince = null
    this.tailSince = null
    this.lastRecovery = -Infinity
    this.reconfiguringUntil = -Infinity
    this.jitter = 0
    this.streams = new Map()
    this.arrivals = []
    this.lossEvents = []
    this.capacity = null
    this.probe = null
    this.probeSequence = 0
    this.lastProbe = -Infinity
    this.probeInterval = 2000
    this.lastProbeResult = null
    this.started = now()
    this.transitFloors = new Map()
  }
  observe(id, p) {
    if (![p.send_us, p.arrival_us, p.bytes].every(Number.isFinite) || p.bytes <= 0) return
    if (p.ssrc === 0x4d523250) {
      if (this.probe && p.rtp_timestamp === probeToken(this.probeSession, this.probe.id) &&
          !this.probe.packets.some((q) => q.sequence === p.sequence)) this.probe.packets.push(p)
      return
    }
    const now = this.now()
    this.lastPacket = now
    let stream = this.streams.get(id)
    if (stream && stream.ssrc !== p.ssrc) this.configurationChanged(id)
    if (!stream || stream.ssrc !== p.ssrc) {
      stream = { ssrc: p.ssrc, highest: p.sequence, received: new Set(), missing: new Map(), lost: [] }
      this.streams.set(id, stream)
    }
    if (stream.observationDrops != null && p.observation_drops !== stream.observationDrops) {
      stream.highest = p.sequence; stream.missing.clear(); stream.received.clear(); stream.lost = []
      this.resetTrend()
    }
    stream.observationDrops = p.observation_drops
    const delta = ((p.sequence - (stream.highest & 65535) + 32768) & 65535) - 32768
    if (Math.abs(delta) > 4096) {
      stream.highest = p.sequence; stream.received.clear(); stream.missing.clear()
      this.resetTrend()
    }
    const extended = stream.highest + (((p.sequence - (stream.highest & 65535) + 32768) & 65535) - 32768)
    if (stream.received.has(extended)) return
    if (extended > stream.highest) {
      for (let seq = stream.highest + 1; seq < extended; seq++) stream.missing.set(seq, now)
      stream.highest = extended
    }
    stream.received.add(extended)
    this.lossEvents.push({ at: now, received: 1, lost: 0 })
    stream.missing.delete(extended)
    for (const seq of stream.received) if (seq < stream.highest - 4096) stream.received.delete(seq)
    // Size buckets keep serialization time of small SPS packets from being
    // mistaken for the queueing delay of a full-size media packet. Monotonic
    // clock offsets cancel against the best transit seen in the same bucket.
    const bucket = Math.floor(p.bytes / 64)
    const transit = (p.arrival_us - p.send_us) / 1000
    const previousFloor = this.transitFloors.get(bucket)
    const floor = !previousFloor || now - previousFloor.at > 30000 || Math.abs(transit - previousFloor.value) > 10000 ? transit :
      Math.min(previousFloor.value + Math.max(0, now - previousFloor.at) * .00025, transit)
    this.transitFloors.set(bucket, { value: floor, at: now })
    // Epoch differences are telemetry only. GCC continues using monotonic
    // differences/floors; the UI must verify rover/base clock synchronization
    // before displaying these as absolute one-way network delay.
    const epochDelay = Number.isSafeInteger(p.arrival_epoch_us) && p.arrival_epoch_us > 0 &&
      Number.isSafeInteger(p.send_epoch_us) && p.send_epoch_us > 0 ?
      (p.arrival_epoch_us - p.send_epoch_us) / 1000 : null
    this.arrivals.push({ id, at: p.received_at_ms ?? now, bytes: p.bytes,
      arrival_us: p.arrival_us,
      queue_ms: Math.max(0, transit - floor), network_delay_ms: epochDelay != null && epochDelay >= 0 ? epochDelay : null })
    const send = p.send_us / 1000
    const arrival = p.arrival_us / 1000
    if (this.group && (send < this.group.first - 1000 || send - this.group.first > 2000)) this.resetTrend()
    if (!this.group) this.group = { first: send, send, arrival }
    else if (send >= this.group.first && send - this.group.first <= 5) {
      this.group.send = Math.max(send, this.group.send)
      this.group.arrival = Math.max(arrival, this.group.arrival)
    } else if (send > this.group.first) {
      this.finishGroup(this.group)
      this.group = { first: send, send, arrival }
    }
  }
  configurationChanged(id = null) {
    if (id != null) {
      // A new encoder SSRC changes only that camera's sequence space. Sender
      // monotonic timestamps and the physical link's queue remain continuous.
      this.streams.delete(id)
      return
    }
    // Camera restart/selection is an application transition, not evidence of
    // network congestion. Retain the budget while the new encoder warms up.
    this.resetTrend()
    this.reconfiguringUntil = this.now() + 300
  }
  resetTrend() {
    this.group = null; this.previous = null; this.points = []
    this.accumulated = 0; this.smoothed = 0; this.trend = 0
    this.overuseSince = null; this.tailSince = null; this.jitter = 0
  }
  finishGroup(group) {
    if (this.previous) {
      const sendDelta = group.send - this.previous.send
      const arrivalDelta = group.arrival - this.previous.arrival
      if (sendDelta > 0 && arrivalDelta >= 0) {
        const delta = arrivalDelta - sendDelta
        this.jitter = .95 * this.jitter + .05 * Math.abs(delta)
        this.accumulated += delta
        this.smoothed = .9 * this.smoothed + .1 * this.accumulated
        this.points.push({ x: group.arrival, y: this.smoothed })
        if (this.points.length > 60) this.points.shift()
        if (this.points.length >= 10) {
          const n = this.points.length
          const x0 = this.points[0].x
          const mx = this.points.reduce((s, p) => s + p.x - x0, 0) / n
          const my = this.points.reduce((s, p) => s + p.y, 0) / n
          const variance = this.points.reduce((s, p) => s + (p.x - x0 - mx) ** 2, 0)
          if (variance > 0) this.trend = 1000 * this.points.reduce((s, p) => s + (p.x - x0 - mx) * (p.y - my), 0) / variance
        }
      }
    }
    this.previous = group
  }
  update(active = true, demandKbps = this.max) {
    const now = this.now()
    const elapsed = Math.min(1, Math.max(0, now - this.lastUpdate) / 1000)
    this.lastUpdate = now
    this.arrivals = this.arrivals.filter((p) => p.at > now - 1000)
    let missing = 0
    for (const s of this.streams.values()) {
      for (const [seq, at] of s.missing) {
        if (now - at >= 100) {
          s.lost.push(now); s.missing.delete(seq)
          this.lossEvents.push({ at: now, received: 0, lost: 1 })
        }
      }
      s.lost = s.lost.filter((at) => at > now - 1000)
      missing += s.lost.length
    }
    const span = this.arrivals.length ? Math.max(200, Math.min(1000, now - this.arrivals[0].at)) : 1000
    const receive = this.arrivals.reduce((s, p) => s + p.bytes * 8, 0) / span
    const receiveByStream = {}
    for (const p of this.arrivals)
      receiveByStream[p.id] = (receiveByStream[p.id] || 0) + p.bytes * 8 / span
    const loss = missing / Math.max(1, missing + this.arrivals.length)
    // RF corruption can lose a few packets even on an uncongested link.
    // Smooth loss evidence over five seconds so a sparse 3 FPS stream or a
    // short random burst does not repeatedly suppress capacity discovery.
    // Queue delay still reacts immediately; telemetry retains its 1 s ratio.
    this.lossEvents = this.lossEvents.filter(p => p.at > now - 5000)
    const lossPackets = this.lossEvents.reduce((n,p) => n + p.received + p.lost, 0)
    const decisionLoss = lossPackets >= 20 ?
      this.lossEvents.reduce((n,p) => n + p.lost, 0) / lossPackets : 0
    const fresh = this.lastPacket !== null && now - this.lastPacket < 500
    const spanMs = this.points.length > 1 ? this.points.at(-1).x - this.points[0].x : 0
    const growth = this.points.length > 1 ? this.points.at(-1).y - this.points[0].y : 0
    const rising = this.points.length >= 10 && spanMs >= 300 &&
      this.trend > Math.max(12, 3 * this.jitter * 1000 / spanMs) && growth > Math.max(3, 2 * this.jitter)
    if (!rising) this.overuseSince = null
    else if (this.overuseSince === null) this.overuseSince = now
    const queues = this.arrivals.map((p) => p.queue_ms).sort((a, b) => a - b)
    const queue = queues.length ? queues[Math.floor(queues.length * .5)] : 0
    const delays = this.arrivals.map((p) => p.network_delay_ms).filter((v) => v != null).sort((a, b) => a - b)
    const queueLimit = 20
    const recent = this.arrivals.filter((p) => p.at > now - 250).map((p) => p.queue_ms).sort((a,b) => a-b)
    const tail = recent.length >= 20 ? recent[Math.ceil(recent.length*.95)-1] : 0
    if (fresh && tail > 60) this.tailSince ??= now
    else this.tailSince = null
    const sustainedTail = this.tailSince !== null && now - this.tailSince >= 400
    const overuse = now >= this.reconfiguringUntil &&
      ((rising && now - this.overuseSince >= 150) || (fresh && queue > queueLimit) || sustainedTail)
    const ceiling = Math.max(this.min, Math.min(this.max, demandKbps))
    if (this.capacity !== null && receive > this.capacity * 1.2) this.capacity = null
    this.budget = Math.min(this.budget, ceiling)
    // GoogCC uses multiplicative discovery, additive increase near a learned
    // capacity, and throughput-based multiplicative backoff. Application-
    // limited traffic holds the estimate; measured probe clusters can raise it.
    if (active && (overuse || decisionLoss > .1)) {
      if (now - this.lastDecrease >= 400) {
        this.capacity = receive > 0 ? receive : this.budget
        this.budget = Math.max(this.min, Math.min(this.budget, receive > 0 ? receive * .85 - 5 : this.budget * .85))
        this.lastDecrease = now
        this.probe = null; this.probeInterval = 5000
      }
    } else if (active && fresh && !overuse && decisionLoss <= .1 && now - this.lastDecrease >= 600 &&
        !(this.trend < -12 && growth < -3)) {
      const limit = Math.max(this.budget, 1.5 * receive + 10)
      const near = this.capacity !== null && this.budget >= this.capacity * .85
      const packetKbits = this.arrivals.length ?
        this.arrivals.reduce((n,p)=>n+p.bytes*8,0)/this.arrivals.length/1000 : 1
      const additive = Math.max(4, packetKbits / .6)
      // At the lowest profiles even one media packet takes hundreds of ms.
      // Discover with short probes; passive growth is allowed only below an
      // already measured conservative capacity, at no more than 4% per s.
      const increase = !elapsed ? 0 : this.budget < 200 ?
        (this.capacity !== null && this.budget < this.capacity * .85 ? this.budget * .04 * elapsed : 0) :
        near ? additive * elapsed : Math.max(1, this.budget * (Math.pow(1.08, elapsed) - 1))
      this.budget = Math.min(ceiling, limit, this.budget + increase)
    }
    if (!active) this.probe = null
    if (this.probe && now - this.probe.started >= 800) {
      const probe = this.probe, packets = probe.packets.sort((a, b) => a.send_us - b.send_us)
      const first = packets[0], last = packets.at(-1)
      if (packets.length >= (probe.target < 150 ? 3 : 5) && last.send_us - first.send_us >= 30000) {
        const sendSpan = (last.send_us - first.send_us) / 1000
        const arrivalSpan = (Math.max(...packets.map((p) => p.arrival_us)) - Math.min(...packets.map((p) => p.arrival_us))) / 1000
        const firstArrival = Math.min(...packets.map((p) => p.arrival_us))
        const lastArrival = Math.max(...packets.map((p) => p.arrival_us))
        const probeBits = packets.filter(p=>p.arrival_us>firstArrival).reduce((s,p)=>s+p.bytes*8,0)
        const mediaBits = this.arrivals.filter(p=>p.arrival_us>firstArrival&&p.arrival_us<=lastArrival)
          .reduce((s,p)=>s+p.bytes*8,0)
        // Measure aggregate delivery in the SAME probe window. Adding a prior
        // one-second media rate can count media that was blocked behind the
        // probe, falsely declaring capacity that the radio never delivered.
        const achieved = (probeBits + mediaBits) / Math.max(sendSpan, arrivalSpan, 1)
        const loss = 1 - packets.length / Math.max(packets.length, last.sequence - first.sequence + 1)
        const success = achieved >= probe.target * .8 && arrivalSpan - sendSpan < 20 && loss < .1
        this.lastProbeResult = { id: probe.id, achieved_kbps: achieved, success }
        if (success && now - this.lastDecrease >= 1000) {
          if (this.capacity !== null && achieved > this.capacity * 1.2) this.capacity = null
          const previous = this.budget
          this.budget = Math.min(ceiling, Math.max(this.budget, achieved * .9))
          this.probeInterval = this.budget > previous * 1.05 ? 2000 : 5000
          if (this.probeInterval === 5000) this.capacity = achieved
        } else {
          this.capacity = Math.max(this.budget, achieved * .85)
          this.probeInterval = 5000
        }
      } else this.probeInterval = 5000
      this.probe = null
    }
    if (active && !this.probe && !overuse && decisionLoss <= .1 && now - this.started >= 1500 &&
        now >= this.reconfiguringUntil && now - this.lastDecrease >= 2000 &&
        now - this.lastProbe >= this.probeInterval && this.budget < ceiling * .95) {
      // Low-rate clusters need enough serialization time for three packets.
      // Moderate links use a smaller rate jump; at high rates a 50 ms cluster
      // can discover headroom quickly without a long burst of queued traffic.
      const discoveryScale = this.budget < 60 || this.budget >= 1000 ? 2 : 1.5
      this.probe = { id: this.nextProbeId(), started: now,
        baselineReceive: receive,
        duration: this.budget < 200 ? 60 : 50,
        target: Math.min(ceiling, Math.max(this.budget + 10, this.budget * (this.budget < 200 ? 2 : this.capacity ? 1.25 : discoveryScale))), packets: [] }
      this.lastProbe = now
    }
    return { budget_kbps: this.budget, receive_kbps: receive, receive_by_stream_kbps: receiveByStream,
      trend_ms_s: fresh ? this.trend : 0, queue_ms: queue, loss,
      network_delay_ms: delays.length ? delays[Math.floor((delays.length - 1) * .5)] : null,
      network_delay_p95_ms: delays.length ? delays[Math.ceil(delays.length * .95) - 1] : null,
      network_delay_min_ms: delays.length ? delays[0] : null,
      network_delay_samples: delays.length,
      probe_id: this.probe?.id || 0, probe_kbps: this.probe?.target || 0, probe_duration_ms: this.probe?.duration || 0 }
  }
  nextProbeId() { this.probeSequence = this.probeSequence % 65535 + 1; return this.probeSequence }
}

class PacketObservations {
  constructor(onPacket) { this.buffer = Buffer.alloc(0); this.onPacket = onPacket }
  push(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk])
    while (this.buffer.length >= 52) {
      const b = this.buffer.subarray(0, 52)
      this.buffer = this.buffer.subarray(52)
      if (b.readUInt32BE(44) !== 0x4d523201) { this.buffer = Buffer.alloc(0); throw Error('invalid RTP observation') }
      this.onPacket({ arrival_us: Number(b.readBigUInt64BE(0)), arrival_epoch_us: Number(b.readBigUInt64BE(8)),
        send_us: Number(b.readBigUInt64BE(16)), send_epoch_us: Number(b.readBigUInt64BE(24)),
        observation_drops: b.readUInt32BE(48), ssrc: b.readUInt32BE(32), rtp_timestamp: b.readUInt32BE(36), sequence: b.readUInt16BE(40), bytes: b.readUInt16BE(42) })
    }
  }
}
module.exports = { DelayController, PacketObservations }
