import assert from 'node:assert/strict'
import { VideoStartupQueue } from '../src/lib/videoStartupQueue'
import type { VideoChunkMessage } from '../src/lib/videoProtocol'
let time = 0
const q = new VideoStartupQueue(() => time)
const chunk = (n: number, key = false, size = 10): VideoChunkMessage => ({ kind: 'chunk', streamId: 'a', timestampUs: n,
  baseIngestTimestampUs: n, browserReceiveEpochUs: 0, correlation: null, key, delta: !key, payload: new Uint8Array(size) })
q.push(chunk(1)); assert.equal(q.take().length, 0)
q.push(chunk(2, true)); q.push(chunk(3)); q.push(chunk(4))
assert.deepEqual(q.take().map(c => c.timestampUs), [2,3,4], 'config readiness must preserve IDR and contiguous deltas')
q.push(chunk(5,true)); q.push(chunk(6)); q.push(chunk(7,true)); q.push(chunk(8))
assert.deepEqual(q.take().map(c => c.timestampUs), [7,8])
q.push(chunk(10,true)); time=151; assert.equal(q.take().length,0)
time=200; q.push(chunk(11,true)); assert.equal(q.push(chunk(12,false,600000)),false); assert.equal(q.take().length,0)
q.push(chunk(13,true)); for(let i=0;i<5;i++)assert(q.push(chunk(14+i)))
assert.equal(q.push(chunk(20)),false); assert.equal(q.take().length,0)
console.log('bounded startup sequence / IDR ordering tests passed')
