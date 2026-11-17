/** Convert one Annex B picture to AVC. Its SPS/PPS live in the decoder config.
 * RTP/h264parse can prepend SPS/PPS before an AUD. Keeping that AUD would make
 * Chromium see a separate, parameter-only access unit before the actual IDR.
 */
export function annexBToAvcc(bytes: Uint8Array) {
  const starts: Array<{ at: number; size: number }> = []
  for (let i = 0; i + 2 < bytes.length; i++) {
    if (bytes[i] || bytes[i + 1]) continue
    const size = bytes[i + 2] === 1 ? 3 :
      i + 3 < bytes.length && bytes[i + 2] === 0 && bytes[i + 3] === 1 ? 4 : 0
    if (size) { starts.push({ at: i, size }); i += size - 1 }
  }
  const nals = starts.map((s, i) => bytes.subarray(s.at + s.size, starts[i + 1]?.at ?? bytes.length))
    .filter(nal => nal.length && ![7, 8, 9].includes(nal[0] & 31))
  const output = new Uint8Array(nals.reduce((n, nal) => n + 4 + nal.length, 0))
  const view = new DataView(output.buffer)
  let at = 0
  for (const nal of nals) {
    view.setUint32(at, nal.length); output.set(nal, at + 4); at += nal.length + 4
  }
  return output
}
