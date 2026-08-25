export const DOWNLOAD_URL_LIFETIME_MS = 30_000

export interface DownloadBlobOptions {
  filename: string
  expectedMimeType?: string
}

export function downloadBlob(blob: Blob, options: DownloadBlobOptions): void {
  if (!(blob instanceof Blob) || blob.size <= 0) {
    throw new Error('导出文件为空，未触发下载')
  }
  const filename = options.filename.trim()
  if (!filename) throw new Error('导出文件名无效')
  if (
    options.expectedMimeType
    && blob.type
    && blob.type !== options.expectedMimeType
  ) {
    throw new Error(`导出文件类型异常：${blob.type}`)
  }

  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.style.display = 'none'
  document.body.append(anchor)
  try {
    anchor.click()
  } catch (cause) {
    URL.revokeObjectURL(url)
    anchor.remove()
    throw cause
  }
  window.setTimeout(() => anchor.remove(), 0)
  window.setTimeout(() => URL.revokeObjectURL(url), DOWNLOAD_URL_LIFETIME_MS)
}
