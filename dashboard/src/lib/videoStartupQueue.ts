import type { VideoChunkMessage } from './videoProtocol'

/** Keep a short, contiguous decode sequence beginning with the newest IDR. */
export class VideoStartupQueue {
  private chunks: VideoChunkMessage[] = []
  private bytes = 0
  private started = 0
  constructor(private readonly now = () => performance.now()) {}

  clear() { this.chunks = []; this.bytes = 0; this.started = 0 }

  push(chunk: VideoChunkMessage): boolean {
    if (chunk.key) { this.clear(); this.started = this.now() }
    if (!chunk.key && this.chunks.length === 0) return true
    this.chunks.push(chunk); this.bytes += chunk.payload.byteLength
    if (this.chunks.length > 6 || this.bytes > 512 * 1024 || this.now() - this.started > 150) {
      this.clear(); return false
    }
    return true
  }

  take(): VideoChunkMessage[] {
    const chunks = this.now() - this.started <= 150 ? this.chunks : []
    this.clear(); return chunks
  }
}
