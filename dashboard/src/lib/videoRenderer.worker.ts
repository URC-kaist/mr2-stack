type Draw = { type: 'frame'; frame: VideoFrame } | { type: 'init'; canvas: OffscreenCanvas; streamId: string }
const scope = self as unknown as { onmessage: ((event: MessageEvent<Draw>) => void) | null; postMessage: (value: unknown) => void }
let canvas: OffscreenCanvas | null = null
let context: OffscreenCanvasRenderingContext2D | null = null
let streamId = ''
scope.onmessage = ({ data }) => {
  if (data.type === 'init') {
    canvas = data.canvas; streamId = data.streamId
    context = canvas.getContext('2d', { alpha: false })
    if (!context) scope.postMessage({ type: 'error', message: 'Video canvas is unavailable' })
    return
  }
  const frame = data.frame
  try {
    if (!canvas || !context) return
    const width = frame.displayWidth || frame.codedWidth
    const height = frame.displayHeight || frame.codedHeight
    if (canvas.width !== width) canvas.width = width
    if (canvas.height !== height) canvas.height = height
    context.drawImage(frame, 0, 0, width, height)
    scope.postMessage({ type: 'video-rendered', streamId, timestampUs: frame.timestamp,
      renderEpochUs: Math.round((performance.timeOrigin + performance.now()) * 1000), width, height })
  } catch (error) {
    scope.postMessage({ type: 'error', message: String(error) })
  } finally { frame.close() }
}
