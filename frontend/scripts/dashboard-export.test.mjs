import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'

import {
  DOWNLOAD_URL_LIFETIME_MS,
  downloadBlob,
} from '../src/utils/downloadBlob.ts'

test('attaches a download anchor and keeps its Blob URL alive for 30 seconds', () => {
  const originalDocument = globalThis.document
  const originalWindow = globalThis.window
  const originalCreateObjectURL = URL.createObjectURL
  const originalRevokeObjectURL = URL.revokeObjectURL
  const scheduled = []
  const appended = []
  let clicked = 0
  let revoked = ''
  const anchor = {
    href: '',
    download: '',
    style: { display: '' },
    click: () => { clicked += 1 },
    remove: () => {},
  }
  globalThis.document = {
    createElement: () => anchor,
    body: { append: (value) => appended.push(value) },
  }
  globalThis.window = {
    setTimeout: (callback, delay) => {
      scheduled.push({ callback, delay })
      return scheduled.length
    },
  }
  URL.createObjectURL = () => 'blob:dashboard-export'
  URL.revokeObjectURL = (value) => { revoked = value }
  try {
    downloadBlob(new Blob(['ok'], { type: 'application/json;charset=utf-8' }), {
      filename: 'result.json',
      expectedMimeType: 'application/json;charset=utf-8',
    })
    assert.deepEqual(appended, [anchor])
    assert.equal(clicked, 1)
    assert.equal(anchor.download, 'result.json')
    assert.ok(scheduled.some((item) => item.delay === DOWNLOAD_URL_LIFETIME_MS))
    assert.equal(revoked, '')
    scheduled.find((item) => item.delay === DOWNLOAD_URL_LIFETIME_MS).callback()
    assert.equal(revoked, 'blob:dashboard-export')
  } finally {
    globalThis.document = originalDocument
    globalThis.window = originalWindow
    URL.createObjectURL = originalCreateObjectURL
    URL.revokeObjectURL = originalRevokeObjectURL
  }
})

test('both dashboard exports use the shared reliable download helper', async () => {
  const [left, right] = await Promise.all([
    readFile(new URL('../src/components/dashboard/LeftSidebarPanel.vue', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/dashboard/RightSidebarPanel.vue', import.meta.url), 'utf8'),
  ])
  assert.match(left, /downloadBlob\(blob/)
  assert.match(right, /downloadBlob\(blob/)
  assert.doesNotMatch(right, /URL\.revokeObjectURL/)
})

