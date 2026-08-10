// 事件持续时间和展示名称

import assert from 'node:assert/strict'
import test from 'node:test'

function formatDetectedEventDuration(seconds) {
  const total = Math.max(0, Math.floor(seconds))
  if (total < 60) return `${total} 秒`
  const minutes = Math.floor(total / 60)
  const remain = total % 60
  return `${minutes} 分 ${remain} 秒`
}

function detectedEventDurationSeconds(snapshot, card) {
  if (snapshot && Number.isFinite(snapshot.elapsed_seconds)) {
    return Math.max(0, snapshot.elapsed_seconds - card.start_seconds)
  }
  return Math.max(0, Number(card.duration_seconds) || 0)
}

function detectedEventTypeLabel(card) {
  if (card.display_label) {
    if (card.display_label === '局部占道') return '疑似局部阻塞'
    return card.display_label
  }
  const labels = {
    localized_blockage: '疑似局部阻塞',
    spillback: '排队溢出',
  }
  return labels[card.traffic_state] ?? card.traffic_state
}

test('formats 65 seconds as 1 分 5 秒', () => {
  assert.equal(formatDetectedEventDuration(65), '1 分 5 秒')
  assert.equal(formatDetectedEventDuration(5), '5 秒')
})

test('duration uses simulation elapsed - stable start', () => {
  const card = { start_seconds: 100, duration_seconds: 10 }
  assert.equal(detectedEventDurationSeconds({ elapsed_seconds: 165 }, card), 65)
  assert.equal(
    formatDetectedEventDuration(detectedEventDurationSeconds({ elapsed_seconds: 165 }, card)),
    '1 分 5 秒',
  )
})

test('display label uses 疑似局部阻塞', () => {
  assert.equal(detectedEventTypeLabel({ traffic_state: 'localized_blockage' }), '疑似局部阻塞')
  assert.equal(detectedEventTypeLabel({ traffic_state: 'x', display_label: '局部占道' }), '疑似局部阻塞')
})
