import type { ReactNode } from 'react'
import { useCallback, useEffect, useState } from 'react'
import { FiRefreshCw } from 'react-icons/fi'
import VideoStreamCard, { type VideoPlaybackStats } from './VideoStreamCard'
import VideoLinkCard from './VideoLinkCard'
import { useXbeeGateway } from '../hooks/useXbeeGateway'
import { useVideoStreams } from '../hooks/useVideoStreams'
import { getVideoGatewayClient, type VideoStreamInfo } from '../lib/videoGateway'

type ConfiguredVideoGridProps = {
  title: string
  panel?: string
  large?: boolean
  showReconnect?: boolean
  showStreamControls?: boolean
  showLinkStatus?: boolean
}

const ConfiguredVideoGrid = ({
  title,
  panel,
  large = false,
  showReconnect = false,
  showStreamControls = false,
  showLinkStatus = false,
}: ConfiguredVideoGridProps) => {
  const { gateway, connected } = useXbeeGateway()
  const { streams, loading, error, refresh } = useVideoStreams(panel)
  const [pendingStreams, setPendingStreams] = useState<Set<string>>(() => new Set())
  const [toggleError, setToggleError] = useState<string | null>(null)
  const [playbackStats, setPlaybackStats] = useState<Record<string, VideoPlaybackStats>>({})

  const handlePlaybackStats = useCallback((streamId: string, stats: VideoPlaybackStats) => {
    setPlaybackStats((current) => ({ ...current, [streamId]: stats }))
  }, [])

  const handleReconnect = () => {
    getVideoGatewayClient().reconnect()
    refresh()
  }

  const setStreamPending = useCallback((streamId: string, pending: boolean) => {
    setPendingStreams((current) => {
      const next = new Set(current)
      if (pending) {
        next.add(streamId)
      } else {
        next.delete(streamId)
      }
      return next
    })
  }, [])

  useEffect(() => gateway.onVideoCommandResult((result) => {
    setStreamPending(result.stream_id, false)
    if (!result.success) setToggleError(result.message)
    refresh()
  }), [gateway, refresh, setStreamPending])

  useEffect(() => gateway.onConnectionStatus((isConnected) => {
    if (!isConnected) setPendingStreams(new Set())
  }), [gateway])

  const setStream = (stream: VideoStreamInfo, enabled: boolean, priority: 'high' | 'low') => {
    setToggleError(null)
    if (!gateway.sendVideoSet(stream.stream_id, enabled, priority)) {
      setToggleError('Control connection unavailable')
      return
    }
    setStreamPending(stream.stream_id, true)
  }

  const renderStream = (stream: VideoStreamInfo) => {
    const isEnabled = stream.enabled ?? stream.state === 'ON'
    const pending = pendingStreams.has(stream.stream_id)
    const playback = playbackStats[stream.stream_id]
    const hasVideo = playback?.width && playback.height && playback.fps > 0 &&
      stream.state !== 'OFF' && stream.state !== 'NO DEVICE'

    return (
      <div className="video-feed-item" key={stream.stream_id}>
        <div className="video-feed-item-header">
          <span className="video-feed-label">
            {stream.display.label || stream.stream_id}
          </span>
          {showStreamControls && (
            <>
              <span
                className="video-feed-quality"
                title="Displayed resolution and measured playback FPS (last second)"
              >
                {hasVideo ? `${playback.width}×${playback.height} · ${playback.fps.toFixed(1)} FPS` : '— · 0 FPS'}
              </span>
              <select
                aria-label={`${stream.display.label || stream.stream_id} priority`}
                value={stream.priority || 'low'}
                disabled={!connected || pending || stream.state == null}
                onChange={(event) => setStream(stream, isEnabled, event.target.value as 'high' | 'low')}
              >
                <option value="high">High</option>
                <option value="low">Low</option>
              </select>
              <button
                className="video-feed-stream-toggle"
                type="button"
                disabled={!connected || pending || stream.state == null}
                onClick={() => setStream(stream, !isEnabled, stream.priority || 'low')}
              >
                {pending ? 'Applying…' : isEnabled ? 'Disable' : 'Enable'}
              </button>
            </>
          )}
        </div>
        <VideoStreamCard
          stream={stream}
          videoWidth={stream.width ?? 320}
          videoHeight={stream.height ?? 180}
          onPlaybackStats={showStreamControls ? handlePlaybackStats : undefined}
        />
      </div>
    )
  }

  let content: ReactNode

  if (loading) {
    content = <div className="video-feed-placeholder">Loading video streams...</div>
  } else if (error) {
    content = <div className="video-feed-placeholder">{error}</div>
  } else if (streams.length === 0) {
    content = (
      <div className="video-feed-placeholder">
        No streams configured{panel ? ` for ${panel}` : ''}
      </div>
    )
  } else {
    content = (
      <>{streams.map(renderStream)}</>
    )
  }

  return (
    <article className={`video-feed-card ${large ? 'video-feed-card--large' : 'card'}`}>
      <header className="video-feed-header">
        <h3>{title}</h3>
        {showReconnect && (
          <button
            className="video-feed-reconnect"
            type="button"
            aria-label="Reconnect live feed"
            title="Reconnect live feed"
            onClick={handleReconnect}
          >
            <FiRefreshCw aria-hidden="true" />
            <span>Reconnect</span>
          </button>
        )}
      </header>
      {showStreamControls && toggleError && (
        <div className="video-feed-toggle-error">{toggleError}</div>
      )}
      {showLinkStatus || (!loading && !error && streams.length > 0) ? (
        <div className="video-feed-grid">
          {showLinkStatus && <VideoLinkCard />}
          {content}
        </div>
      ) : content}
    </article>
  )
}

export default ConfiguredVideoGrid
