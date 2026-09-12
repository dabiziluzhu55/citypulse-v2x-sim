import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isUnknownSessionStreamClose,
  resolveSimulationStreamUrl,
  UNKNOWN_SESSION_WS_CLOSE_CODE,
} from '../src/utils/runWebSocketManager.ts'

const httpLocation = {
  protocol: 'http:',
  host: 'localhost:5173',
  origin: 'http://localhost:5173',
}

test('uses the relative websocket URL returned by the backend on the frontend host', () => {
  assert.equal(
    resolveSimulationStreamUrl('session one', '/api/v1/simulations/session%20one/stream', httpLocation),
    'ws://localhost:5173/api/v1/simulations/session%20one/stream',
  )
})

test('normalizes HTTP stream URLs and keeps explicit WebSocket URLs', () => {
  assert.equal(
    resolveSimulationStreamUrl('session-1', 'https://api.example.test/api/v1/stream', httpLocation),
    'wss://api.example.test/api/v1/stream',
  )
  assert.equal(
    resolveSimulationStreamUrl('session-1', 'ws://127.0.0.1:8000/api/v1/stream', httpLocation),
    'ws://127.0.0.1:8000/api/v1/stream',
  )
})

test('falls back to the standard stream path when the backend omits a URL', () => {
  assert.equal(
    resolveSimulationStreamUrl('session one', '', httpLocation),
    'ws://localhost:5173/api/v1/simulations/session%20one/stream',
  )
})

test('treats backend 4004 as a vanished session instead of a reconnectable drop', () => {
  assert.equal(isUnknownSessionStreamClose(UNKNOWN_SESSION_WS_CLOSE_CODE), true)
  assert.equal(isUnknownSessionStreamClose(1011), false)
  assert.equal(isUnknownSessionStreamClose(1006), false)
})
