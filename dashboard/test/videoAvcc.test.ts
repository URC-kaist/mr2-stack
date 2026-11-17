import assert from 'node:assert/strict'
import { annexBToAvcc } from '../src/lib/videoAvcc'

// Real capture layout: repeated parameter sets surrounding an access delimiter.
const au = new Uint8Array([0,0,0,1,0x67,0x42,0,31, 0,0,1,0x68,0xce,
  0,0,0,1,0x09,0xf0, 0,0,1,0x67,0x42,0,31, 0,0,1,0x68,0xce,
  0,0,1,0x06,0x80, 0,0,0,1,0x65,0x88,0,0,3,1])
assert.deepEqual([...annexBToAvcc(au)], [0,0,0,2,0x06,0x80, 0,0,0,6,0x65,0x88,0,0,3,1])
assert.deepEqual([...annexBToAvcc(new Uint8Array([0,0,1,0x41,0xaa]))], [0,0,0,2,0x41,0xaa])
assert.equal(annexBToAvcc(new Uint8Array([0,0,1,0x67,0x42])).length, 0)
console.log('AVC picture framing / parameter-only prefix regression tests passed')
