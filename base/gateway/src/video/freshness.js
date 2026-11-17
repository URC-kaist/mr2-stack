'use strict'

const PROBE_SSRC = 0x4d523250
const MAX_PROBE_RTT_MS = 350
const ANCHOR_LIFETIME_MS = 15000
const DRIFT_PPM = 250

// A probe is requested on XBee and returned on the video UDP path. Its issue
// time and arrival are on the base's monotonic clock. A fast reply bounds the
// rover/base monotonic-clock offset without NTP or an Internet connection.
class LinkFreshnessGuard {
  constructor({ now, nextProbeId, token = id => id }) {
    this.now = now
    this.nextProbeId = nextProbeId
    this.token = token
    this.mode = 'bootstrap'
    this.anchor = null
    this.requests = new Map()
    this.lastProbeAt = -Infinity
    this.refreshOffsetMs = 0
    this.lastMediaAt = null
    this.started = null
    this.recovered = false
    this.severeSince = null
    this.ages = []
    this.transitFloors = new Map()
    this.resumeKbps = 32
    this.barrierAt = -Infinity
  }
  observe(p) {
    const now = this.now()
    if (p.ssrc === PROBE_SSRC) {
      const issued = this.requests.get(p.rtp_timestamp)
      if (issued == null || issued < this.barrierAt || !Number.isFinite(p.send_us) || !Number.isFinite(p.arrival_us)) return
      const rtt = p.arrival_us / 1000 - issued
      if (rtt < 0 || rtt > MAX_PROBE_RTT_MS) return
      // The offset can drift between probe transmission and arrival too.
      // Charge the whole request/reply interval before anchoring at arrival.
      const lower = issued * 1000 - p.send_us - rtt * DRIFT_PPM / 1000
      const old = this.anchor && this.anchor.lower - Math.max(0, now - this.anchor.at) * DRIFT_PPM / 1000
      const upper = p.arrival_us - p.send_us
      this.anchor = { lower: old != null && old <= upper ? Math.max(lower, old) : lower, at: p.arrival_us / 1000 }
      if (this.mode !== 'ready') this.recovered = true
      return
    }
    this.lastMediaAt = p.received_at_ms ?? now
    if (this.anchor) {
      const lower = this.anchor.lower - Math.max(0, p.arrival_us / 1000 - this.anchor.at) * DRIFT_PPM / 1000
      const transit = (p.arrival_us - p.send_us) / 1000
      const bucket = Math.floor(p.bytes / 64)
      const previous = this.transitFloors.get(bucket)
      const floor = previous == null || Math.abs(transit - previous.value) > 10000 ? transit :
        Math.min(transit, previous.value + Math.max(0, this.lastMediaAt - previous.at) * .00025)
      this.transitFloors.set(bucket, { value: floor, at: this.lastMediaAt })
      this.ages.push({ at: this.lastMediaAt, bytes: p.bytes, queue: transit - floor,
        age: (p.arrival_us - p.send_us - lower) / 1000 })
      if (this.ages.length > 4096) this.ages.splice(0, this.ages.length - 4096)
    }
  }
  enterDrain(metrics, controller) {
    this.mode = 'draining'
    this.barrierAt = this.now()
    this.resumeKbps = Math.max(controller.min, Math.min(300, metrics.receive_kbps * .5, controller.budget * .5))
    this.recovered = false
    this.lastProbeAt = -Infinity
    this.severeSince = null
    controller.probe = null
  }
  issueProbe(target, now) {
    const id = this.nextProbeId()
    this.requests.set(this.token(id), now)
    this.lastProbeAt = now
    this.refreshOffsetMs = id % 7 * 29
    return { probe_id: id, probe_kbps: target, probe_duration_ms: 100 }
  }
  update(metrics, active, controller, expectedFrameMs = 1000 / 3) {
    const now = this.now()
    for (const [id, at] of this.requests) if (now - at > ANCHOR_LIFETIME_MS) this.requests.delete(id)
    this.ages = this.ages.filter((p) => p.at > now - 300)
    if (!active) {
      this.mode = 'bootstrap'; this.anchor = null; this.started = null
      this.recovered = false; this.resumeKbps = controller.min
      this.lastMediaAt = null; this.severeSince = null; this.ages = []
      this.requests.clear(); this.barrierAt = -Infinity; this.lastProbeAt = -Infinity
      controller.probe = null
      return { ...metrics, budget_kbps: 0, probe_id: 0, probe_kbps: 0, probe_duration_ms: 0 }
    }
    this.started ??= now
    if (this.recovered) {
      this.recovered = false; this.mode = 'ready'
      controller.budget = Math.min(controller.max, Math.max(controller.min, this.resumeKbps))
      controller.capacity = null; controller.probe = null
      controller.lastDecrease = now; controller.probeInterval = 2000
      controller.resetTrend()
      // A controlled pause plus a fast causal reply permits a new path floor;
      // a bitrate reduction alone never does. The packet age gate still
      // rejects old packets from another radio queue after this reset.
      controller.transitFloors.clear(); this.transitFloors.clear()
      this.lastMediaAt = now; this.severeSince = null; this.ages = []
      metrics = { ...metrics, budget_kbps: controller.budget, probe_id: 0 }
    }
    if (this.mode === 'ready') {
      const sorted = this.ages.map((p) => p.age).sort((a, b) => a - b)
      const oldMedia = sorted.length >= 3 && sorted[Math.floor(sorted.length / 2)] > 450
      const queued = metrics.queue_ms > 150 && this.lastMediaAt != null && now - this.lastMediaAt < 500
      const recentQueue = this.ages.map((p) => p.queue).sort((a,b) => a-b)
      const fastReceive = this.ages.reduce((n,p) => n + p.bytes * 8, 0) / 300
      if (recentQueue.length >= 2 && recentQueue[Math.floor(recentQueue.length / 2)] > 40 &&
          fastReceive < controller.budget * .4 && controller.budget > controller.min) {
        controller.budget = Math.max(controller.min, Math.min(controller.budget, fastReceive * .7))
        controller.probe = null; controller.lastDecrease = now
        metrics = { ...metrics, budget_kbps: controller.budget, probe_id: 0 }
      }
      if (oldMedia || queued) this.severeSince ??= now
      else this.severeSince = null
      const silent = now - (this.lastMediaAt ?? this.started)
      if (silent > Math.max(300, expectedFrameMs * 2.5) && controller.budget > 300) {
        // Stop a high-rate sender before a silent failed radio fills its queue.
        controller.budget = Math.max(controller.min, Math.min(300, metrics.receive_kbps * .5))
        controller.probe = null; controller.lastDecrease = now
        metrics = { ...metrics, budget_kbps: controller.budget, probe_id: 0 }
      }
      if (!this.anchor || now - this.anchor.at > ANCHOR_LIFETIME_MS || silent > 1200 ||
          (this.severeSince !== null && now - this.severeSince >= 200)) this.enterDrain(metrics, controller)
    }
    if (this.mode !== 'ready') {
      controller.probe = null
      const probe = now - this.lastProbeAt >= 1000 ? this.issueProbe(8, now) :
        { probe_id: 0, probe_kbps: 0, probe_duration_ms: 0 }
      return { ...metrics, budget_kbps: 0, ...probe }
    }
    if (metrics.probe_id) {
      if (!this.requests.has(this.token(metrics.probe_id))) {
        this.requests.set(this.token(metrics.probe_id), now); this.lastProbeAt = now
        this.refreshOffsetMs = metrics.probe_id % 7 * 29
      }
    } else if (now - this.lastProbeAt >= 2000 + this.refreshOffsetMs) {
      // At the configured ceiling, capacity discovery stops. Keep the causal
      // freshness bound alive with about 100 bytes every two seconds.
      const target = metrics.budget_kbps < 200 ? 8 : Math.ceil(metrics.receive_kbps + 8)
      metrics = { ...metrics, ...this.issueProbe(target, now) }
    }
    return metrics
  }
  policy() {
    return { paused: this.mode !== 'ready',
      lower_bound_us: this.anchor?.lower ?? 0,
      anchor_at_us: (this.anchor?.at ?? 0) * 1000,
      valid_until_us: this.anchor ? (this.anchor.at + ANCHOR_LIFETIME_MS) * 1000 : 0 }
  }
}

module.exports = { LinkFreshnessGuard, MAX_PROBE_RTT_MS }
