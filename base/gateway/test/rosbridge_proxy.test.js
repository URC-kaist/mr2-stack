'use strict'
const test=require('node:test')
const assert=require('node:assert/strict')
const http=require('node:http')
const {once}=require('node:events')
const {WebSocket,WebSocketServer}=require('ws')
const {createWsRouteRegistry}=require('../src/runtime/ws_route_registry')
const {createRosbridgeProxy}=require('../src/runtime/rosbridge_proxy')
test('same-origin proxy forwards rosbridge text and binary to the explicit rover', async () => {
  const upstream=new WebSocketServer({port:0,host:'127.0.0.1'})
  await once(upstream,'listening')
  upstream.on('connection',ws=>ws.on('message',(data,binary)=>ws.send(data,{binary})))
  const server=http.createServer(); const routes=createWsRouteRegistry({server})
  const proxy=createRosbridgeProxy({routeRegistry:routes,host:'127.0.0.1',port:upstream.address().port})
  server.listen(0,'127.0.0.1'); await once(server,'listening')
  const client=new WebSocket(`ws://127.0.0.1:${server.address().port}/rosbridge-ws`)
  try {
    await once(client,'open')
    const text=once(client,'message');client.send('{"op":"get_topics"}')
    const [data,binary]=await text;assert.equal(data.toString(),'{"op":"get_topics"}');assert.equal(binary,false)
    const bytes=once(client,'message');client.send(Buffer.from([1,2,3]))
    const [result,isBinary]=await bytes;assert.deepEqual(result,Buffer.from([1,2,3]));assert(isBinary)
  } finally {
    client.terminate();proxy.close();routes.close();server.close();upstream.close()
  }
})
