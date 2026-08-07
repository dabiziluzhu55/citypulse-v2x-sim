import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { shouldApplySimulationSnapshot } from '../src/utils/snapshotOrdering.ts'
import { simulationSnapshotErrorMessage } from '../src/utils/simulationSessionError.ts'

const simulationStoreSource = await readFile(
  new URL('../src/composables/useSimulationStore.ts', import.meta.url),
  'utf8',
)
const leftSidebarSource = await readFile(
  new URL('../src/components/dashboard/LeftSidebarPanel.vue', import.meta.url),
  'utf8',
)

function snapshot(sequence, elapsedSeconds, state = 'RUNNING', sessionId = 'session-1') {
  return {
    session_id: sessionId,
    sequence,
    elapsed_seconds: elapsedSeconds,
    state,
  }
}

test('accepts only monotonic snapshots for the active session', () => {
  const current = snapshot(12, 2.4)
  assert.equal(shouldApplySimulationSnapshot(current, snapshot(13, 2.6), 'session-1'), true)
  assert.equal(shouldApplySimulationSnapshot(current, snapshot(11, 2.2), 'session-1'), false)
  assert.equal(shouldApplySimulationSnapshot(current, snapshot(12, 2.4), 'session-1'), false)
  assert.equal(shouldApplySimulationSnapshot(current, snapshot(12, 2.3), 'session-1'), false)
  assert.equal(shouldApplySimulationSnapshot(current, snapshot(13, 2.6, 'RUNNING', 'old-session'), 'session-1'), false)
})

test('accepts playback state transitions published at the same sequence and time', () => {
  const current = snapshot(40, 8, 'RUNNING')
  assert.equal(shouldApplySimulationSnapshot(current, snapshot(40, 8, 'PAUSED'), 'session-1'), true)
  assert.equal(shouldApplySimulationSnapshot(snapshot(40, 8, 'PAUSED'), snapshot(40, 8, 'RUNNING'), 'session-1'), true)
  assert.equal(shouldApplySimulationSnapshot(current, snapshot(40, 8, 'COMPLETED'), 'session-1'), true)
})

test('keeps asynchronous backend failures actionable and preserves raw details', () => {
  assert.equal(
    simulationSnapshotErrorMessage({
      state: 'FAILED',
      error: 'topology fingerprint mismatch: expected e3c6, got 1756',
    }),
    '算法模型与当前路网拓扑不匹配。后端详情：topology fingerprint mismatch: expected e3c6, got 1756',
  )
  assert.match(
    simulationSnapshotErrorMessage({ state: 'FAILED', error: 'tensor shape mismatch' }),
    /算法模型输入或动作契约不兼容.*tensor shape mismatch/,
  )
  assert.match(
    simulationSnapshotErrorMessage({ state: 'FAILED', error: "No module named 'torch'" }),
    /后端启动环境缺少 PyTorch.*\.venv.*No module named 'torch'/,
  )
  assert.match(
    simulationSnapshotErrorMessage({ state: 'FAILED', error: 'model checkpoint not found' }),
    /算法模型文件缺失.*model checkpoint not found/,
  )
  assert.equal(
    simulationSnapshotErrorMessage({ state: 'RUNNING', error: null }),
    null,
  )

  const applySnapshotBlock = simulationStoreSource.slice(
    simulationStoreSource.indexOf('function applySnapshot'),
    simulationStoreSource.indexOf('function stopPolling'),
  )
  assert.match(applySnapshotBlock, /simulationSnapshotErrorMessage\(next\)/)
  assert.match(applySnapshotBlock, /if \(snapshotFailure\) statusError\.value = snapshotFailure/)
  assert.match(
    applySnapshotBlock,
    /next\.state === 'FAILED' && !next\.error && statusError\.value[\s\S]*Keep the detailed error/,
  )
  assert.match(simulationStoreSource, /unknown session/)
  assert.match(leftSidebarSource, /runtimeFailureStage/)
  assert.match(leftSidebarSource, /算法初始化/)
  assert.match(leftSidebarSource, /后端原始错误/)
})
