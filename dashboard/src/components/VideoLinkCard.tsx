import { useEffect, useState } from 'react'
import { useXbeeGateway } from '../hooks/useXbeeGateway'
import { useLatencyClockReadiness } from '../hooks/useLatencyClockReadiness'
import { getRosBridgeClient } from '../lib/rosBridge'
import { videoLatencyMinimum, videoLinkLatency } from '../lib/videoLinkLatency'
import type { RocketM2Status, VideoLinkMetrics } from '../lib/xbeeGateway'
import './VideoLinkCard.css'

type Sample = {
  at: number
  metrics: VideoLinkMetrics | null
  base: number | null
  rover: number | null
}
type Mode = 'traffic' | 'signal'
type Series = { label: string; color: string; dashed?: boolean; value: (s: Sample) => number | null }

const WINDOW_MS = 60000
const LEFT = 43, RIGHT = 468, TOP = 12, BOTTOM = 124
const mbps = (value: number | null | undefined) => value == null ? '—' : `${(value / 1000).toFixed(2)} Mbps`
const dbm = (value: number | null | undefined) => value == null ? '—' : `${value.toFixed(0)} dBm`
const TRAFFIC: Series[] = [
  { label: 'Received', color: '#35d3c3', value: (s) => s.metrics ? s.metrics.receive_kbps / 1000 : null },
  { label: 'GCC budget', color: '#f5c76b', dashed: true, value: (s) => s.metrics ? s.metrics.budget_kbps / 1000 : null },
]
const SIGNAL: Series[] = [
  { label: 'Base RX', color: '#35d3c3', value: (s) => s.base },
  { label: 'Rover RX', color: '#8db4ff', value: (s) => s.rover },
]

const VideoLinkCard = () => {
  const { gateway } = useXbeeGateway()
  const { readiness } = useLatencyClockReadiness(getRosBridgeClient())
  const [mode, setMode] = useState<Mode>('traffic')
  const [samples, setSamples] = useState<Sample[]>([])
  const [hoverAt, setHoverAt] = useState<number | null>(null)

  useEffect(() => {
    let metrics: VideoLinkMetrics | null = null
    let receivedAt = -Infinity
    const radios: Partial<Record<'base' | 'rover', { status: RocketM2Status; at: number }>> = {}
    const offMetrics = gateway.onVideoLinkMetrics((payload) => {
      metrics = payload
      receivedAt = performance.now()
    })
    const offRadio = gateway.onRocketM2Status((status) => {
      if (status.target !== 'drone') radios[status.target] = { status, at: performance.now() }
    })
    const timer = window.setInterval(() => {
      const at = Date.now(), monotonic = performance.now()
      const connected = gateway.isConnected()
      const signal = (target: 'base' | 'rover') => {
        const radio = radios[target]
        return connected && radio?.status.connected && monotonic - radio.at < 15000 &&
          Number.isFinite(radio.status.signal) ? radio.status.signal : null
      }
      const sample: Sample = { at, base: signal('base'), rover: signal('rover'),
        metrics: connected && monotonic - receivedAt < 2500 ? metrics : null }
      setSamples((current) => [...current.filter((s) => s.at >= at - WINDOW_MS), sample])
    }, 1000)
    return () => { offMetrics(); offRadio(); window.clearInterval(timer) }
  }, [gateway])

  const latest = samples[samples.length - 1]
  const metrics = latest?.metrics
  const latency = videoLinkLatency(metrics, readiness, videoLatencyMinimum.value)
  const series = mode === 'traffic' ? TRAFFIC : SIGNAL
  const end = latest?.at ?? 0, start = end - WINDOW_MS
  const values = samples.flatMap((s) => series.map((line) => line.value(s))).filter((v): v is number => v != null)
  const min = mode === 'signal' ? Math.min(-100, Math.floor(Math.min(-100, ...values) / 10) * 10) : 0
  const max = mode === 'signal' ? Math.max(-20, Math.ceil(Math.max(-20, ...values) / 10) * 10) :
    Math.max(1, Math.ceil(Math.max(0, ...values) * 1.1 * 2) / 2)
  const x = (at: number) => LEFT + (at - start) / WINDOW_MS * (RIGHT - LEFT)
  const y = (value: number) => BOTTOM - (Math.max(min, Math.min(max, value)) - min) / (max - min) * (BOTTOM - TOP)
  const path = (line: Series) => {
    let drawing = false
    return samples.map((s) => {
      const value = line.value(s)
      if (value == null) { drawing = false; return '' }
      const command = `${drawing ? 'L' : 'M'}${x(s.at).toFixed(1)},${y(value).toFixed(1)}`
      drawing = true
      return command
    }).join(' ')
  }
  const hovered = hoverAt == null ? null : samples.reduce<Sample | null>((nearest, s) =>
    !nearest || Math.abs(s.at - hoverAt) < Math.abs(nearest.at - hoverAt) ? s : nearest, null)
  const hasData = samples.some((s) => series.some((line) => line.value(s) != null))

  return (
    <div className="video-feed-item video-link-card">
      <div className="video-feed-item-header">
        <span className="video-feed-label">Network · Rocket M2</span>
        <div className="video-link-modes" role="group" aria-label="Network graph">
          <button type="button" aria-pressed={mode === 'traffic'} onClick={() => setMode('traffic')}>Traffic</button>
          <button type="button" aria-pressed={mode === 'signal'} onClick={() => setMode('signal')}>Signal</button>
        </div>
      </div>
      <div className="video-link-body">
        <div className="video-link-summary">
          <strong>{mode === 'traffic' ? mbps(metrics?.receive_kbps) : dbm(latest?.base)}</strong>
          <span>{mode === 'traffic' ? 'video received · last 1 s' : 'base radio receive signal'}</span>
        </div>
        <div className="video-link-legend">
          {series.map((line) => <span key={line.label} style={{ color: line.color }}>{line.dashed ? '┄' : '━'} {line.label}</span>)}
          <span className="video-link-unit">{mode === 'traffic' ? 'Mbps' : 'dBm'} · 60 s</span>
        </div>
        <div className="video-link-plot">
          <svg viewBox="0 0 480 148" role="img" aria-label={mode === 'traffic' ? 'Video receive rate and GCC budget over the last 60 seconds' : 'Base and rover Rocket M2 signal over the last 60 seconds'}
            onPointerLeave={() => setHoverAt(null)}
            onPointerMove={(event) => {
              const bounds = event.currentTarget.getBoundingClientRect()
              const fraction = Math.max(0, Math.min(1, (480 * (event.clientX - bounds.left) / bounds.width - LEFT) / (RIGHT - LEFT)))
              setHoverAt(start + fraction * WINDOW_MS)
            }}>
            {[0, .5, 1].map((fraction) => {
              const value = min + fraction * (max - min), cy = y(value)
              return <g key={fraction}>
                <line x1={LEFT} x2={RIGHT} y1={cy} y2={cy} className="video-link-gridline" />
                <text x={LEFT - 8} y={cy + 4} textAnchor="end">{mode === 'signal' ? value.toFixed(0) : value.toFixed(1)}</text>
              </g>
            })}
            {[0, .5, 1].map((fraction) => <text key={fraction} x={LEFT + fraction * (RIGHT - LEFT)} y={144} textAnchor={fraction === 0 ? 'start' : fraction === 1 ? 'end' : 'middle'}>
              {fraction === 1 ? 'Now' : `−${60 * (1 - fraction)} s`}
            </text>)}
            {series.map((line) => <path key={line.label} d={path(line)} fill="none" stroke={line.color} strokeWidth="2" strokeDasharray={line.dashed ? '5 4' : undefined} />)}
            {hovered && <line x1={x(hovered.at)} x2={x(hovered.at)} y1={TOP} y2={BOTTOM} className="video-link-cursor" />}
            {!hasData && <text x="255" y="72" textAnchor="middle">{mode === 'traffic' ? 'Waiting for video telemetry' : 'Waiting for radio signal'}</text>}
          </svg>
          {hovered && <div className="video-link-tooltip">
            {new Date(hovered.at).toLocaleTimeString()} · {series.map((line) => {
              const value = line.value(hovered)
              return `${line.label}: ${value == null ? '—' : `${value.toFixed(mode === 'traffic' ? 2 : 0)} ${mode === 'traffic' ? 'Mbps' : 'dBm'}`}`
            }).join(' · ')}
          </div>}
        </div>
        <dl className="video-link-latency" aria-label="Video network latency">
          <div title={latency.title}>
            <dt>Rover → Base</dt><dd>{latency.median}</dd>
          </div>
          <div title={latency.title}>
            <dt>Link P95</dt><dd>{latency.p95}</dd>
          </div>
          <div title="Lowest observed Rover → Base packet delay since this dashboard page started, using synchronized samples only. Retained across tab switches and temporary clock/data loss; a page reload resets it. Separate from the GCC queue baseline.">
            <dt>Min since start</dt><dd>{latency.minimum}</dd>
          </div>
          <div title="Median extra network transit above the observed minimum, last 1 s. Independent of clock offset; not total video latency.">
            <dt>Extra queue</dt><dd>{latency.queue}</dd>
          </div>
        </dl>
        <table className="video-link-classes" aria-label="Video class utilization">
          <thead><tr><th>Class</th><th>Received</th><th title="Wire allocation inferred from the rover's encoder target (85% of allocation), within 1.2 kbps per feed">Budget ≈</th><th title="Received rate / allocated wire budget; a short burst may exceed 100%">Util.</th></tr></thead>
          <tbody>{(['high', 'low'] as const).map((priority) => {
            const group = metrics?.classes[priority]
            return <tr key={priority} data-priority={priority}>
              <th>{priority.toUpperCase()}</th><td>{mbps(group?.receive_kbps)}</td><td>{mbps(group?.budget_kbps)}</td>
              <td>{group?.utilization_pct == null ? '—' : `${group.utilization_pct.toFixed(1)}%`}</td>
            </tr>
          })}</tbody>
        </table>
      </div>
    </div>
  )
}

export default VideoLinkCard
