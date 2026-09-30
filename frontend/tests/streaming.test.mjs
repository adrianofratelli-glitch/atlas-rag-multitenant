import test from 'node:test'
import assert from 'node:assert/strict'
import { streamChat } from '../src/api.js'
const encode = new TextEncoder()
async function run(chunks) {
 const original = globalThis.fetch
 const events = []; let cancelled = false
 globalThis.fetch = async () => new Response(new ReadableStream({
  start(controller) { for (const chunk of chunks) controller.enqueue(encode.encode(chunk)); controller.close() },
  cancel() { cancelled = true },
 }))
 try {
  await streamChat({ question: 'teste' }, { onToken: t => events.push(['token', t]), onDone: () => events.push(['done']), onError: e => events.push(['error', e]) })
  return events
 } finally { globalThis.fetch = original }
}
test('EOF without done reports failure instead of leaving the composer busy', async () => {
 const events = await run(['data: {"type":"token","delta":"parcial"}\n\n'])
 assert.equal(events.at(-1)?.[0], 'error')
})
test('CRLF frames and fragmented Unicode preserve content and terminal event', async () => {
 const events = await run(['data: {"type":"token","delta":"ação"}\r', '\n\r\ndata: {"type":"done"}\r\n\r\n'])
 assert.deepEqual(events, [['token', 'ação'], ['done']])
})
test('error is terminal: a late done cannot turn a failed answer into success', async () => {
 const events = await run(['data: {"type":"error","message":"falhou"}\n\ndata: {"type":"done"}\n\n'])
 assert.deepEqual(events, [['error', 'falhou']])
})
test('broken body is delivered to onError exactly once', async () => {
 const original = globalThis.fetch; const errors = []
 globalThis.fetch = async () => new Response(new ReadableStream({ start(c) { c.error(new Error('socket lost')) } }))
 try { await streamChat({ question: 'teste' }, { onError: e => errors.push(e) }); assert.equal(errors.length, 1) }
 finally { globalThis.fetch = original }
})
test('a cancelled workspace never sends a request', async () => {
 const original = globalThis.fetch; let sent = false; const controller = new AbortController(); controller.abort()
 globalThis.fetch = async () => { sent = true; return new Response('') }
 try { await streamChat({ question: 'teste', signal: controller.signal }, {}); assert.equal(sent, false) }
 finally { globalThis.fetch = original }
})
