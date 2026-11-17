'use strict'
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
module.exports = { PacketObservations }
