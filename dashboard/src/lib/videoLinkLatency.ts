import type { ChronyReadiness } from './chronyStatus'
import type { VideoLinkMetrics } from './xbeeGateway'

const milliseconds = (value: number | null | undefined) =>
  value != null && Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)} ms` : '—'

export class VideoLatencyMinimum {
  value: number | null = null

  observe(metrics: VideoLinkMetrics, clockReady: boolean) {
    const value = metrics.network_delay_min_ms
    if (clockReady && metrics.receive_kbps > 0 && value != null && Number.isFinite(value) && value >= 0) {
      this.value = this.value == null ? value : Math.min(this.value, value)
    }
  }
}

// Page-lifetime history shared by the always-mounted diagnostics panel and
// Live Feed. Navigation between mission tabs must not reset the minimum.
export const videoLatencyMinimum = new VideoLatencyMinimum()

export function videoLinkLatency(
  metrics: VideoLinkMetrics | null | undefined,
  readiness: ChronyReadiness,
  minimumMs: number | null = null,
) {
  const receiving = metrics != null && metrics.receive_kbps > 0
  const clockPending = receiving && !readiness.ready
  return {
    median: clockPending ? 'Clock sync' : milliseconds(receiving ? metrics.network_delay_ms : null),
    p95: clockPending ? 'Clock sync' : milliseconds(receiving ? metrics.network_delay_p95_ms : null),
    minimum: minimumMs == null && clockPending ? 'Clock sync' : milliseconds(minimumMs),
    queue: milliseconds(receiving ? metrics.queue_delay_ms : null),
    title: 'Packet send on rover → receive on base, before jitter buffering; median/P95 over the last 1 s. ' +
      'Excludes camera capture, encoding, sender pacing and browser display. ' +
      (readiness.ready ? 'Rover/base clock synchronization verified.' : 'Clock sync'),
  }
}
