export async function fetchJsonAsset<T>(
  url: string | URL,
  label: string,
  init?: RequestInit,
): Promise<T> {
  const requestedUrl = typeof url === 'string' ? url : url.toString()
  const response = await fetch(url, init)
  const responseUrl = response.url || requestedUrl
  const contentType = response.headers.get('content-type') ?? ''

  if (!response.ok) {
    throw new Error(`${label} 请求失败：${responseUrl}，HTTP ${response.status}`)
  }
  if (!contentType.toLowerCase().includes('json')) {
    throw new Error(
      `${label} 返回类型错误：${responseUrl} 返回 ${contentType || '未知类型'}，预期 JSON`,
    )
  }

  return await response.json() as T
}
