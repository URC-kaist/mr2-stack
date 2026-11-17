export type VideoRendered = { type: 'video-rendered'; streamId: string; timestampUs: number; renderEpochUs: number; width: number; height: number }

/** Ownership of each frame is transferred or closed in every path. */
export function createVideoRenderer(canvas: HTMLCanvasElement, streamId: string,
  rendered: (frame: VideoRendered) => void, failed: () => void, useWorker: boolean) {
  let worker: Worker | null = null
  let context: CanvasRenderingContext2D | null = null
  let pending = 0
  if (useWorker && typeof Worker !== 'undefined' && typeof canvas.transferControlToOffscreen === 'function') {
    worker = new Worker(new URL('./videoRenderer.worker.ts', import.meta.url), { type: 'module' })
    worker.onmessage = ({ data }) => {
      if (data.type === 'video-rendered') { pending = Math.max(0, pending - 1); rendered(data) }
      else if (data.type === 'error') failed()
    }
    worker.onerror = failed
    try {
      const offscreen = canvas.transferControlToOffscreen()
      worker.postMessage({ type: 'init', canvas: offscreen, streamId }, [offscreen])
    } catch (error) { worker.terminate(); throw error }
  } else {
    context = canvas.getContext('2d', { alpha: false })
    if (!context) throw Error('Video canvas is unavailable')
  }
  return {
    draw(frame: VideoFrame) {
      if (worker) {
        // Decoded frames are independent: skip display work when a worker is
        // behind, without dropping compressed reference frames.
        if (pending >= 2) { frame.close(); return }
        ++pending
        try { worker.postMessage({ type: 'frame', frame }, [frame]) }
        catch { --pending; frame.close(); failed() }
        return
      }
      try {
        const width = frame.displayWidth || frame.codedWidth, height = frame.displayHeight || frame.codedHeight
        if (canvas.width !== width) canvas.width = width
        if (canvas.height !== height) canvas.height = height
        context!.drawImage(frame, 0, 0, width, height)
        rendered({ type: 'video-rendered', streamId, timestampUs: frame.timestamp,
          renderEpochUs: Math.round((performance.timeOrigin + performance.now()) * 1000), width, height })
      } finally { frame.close() }
    },
    close() { worker?.terminate(); worker = null },
  }
}
