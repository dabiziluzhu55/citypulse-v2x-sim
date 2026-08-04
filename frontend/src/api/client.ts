export interface ApiResponse<T> {
  data: T
  status: number
}

export interface ApiRequestConfig {
  params?: Record<string, string | number | boolean | null | undefined>
  timeoutMs?: number
}

export interface ApiBlobResponse {
  data: Blob
  status: number
  headers: Headers
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')
const DEFAULT_REQUEST_TIMEOUT_MS = 10_000

function buildUrl(path: string, config?: ApiRequestConfig): string {
  const url = `${API_BASE_URL}${path}`
  if (!config?.params) {
    return url
  }

  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(config.params)) {
    if (value !== null && value !== undefined) {
      query.set(key, String(value))
    }
  }
  const serialized = query.toString()
  return serialized ? `${url}?${serialized}` : url
}

async function request<T>(
  path: string,
  init?: RequestInit,
  config?: ApiRequestConfig,
): Promise<ApiResponse<T>> {
  const controller = new AbortController()
  const timeoutMs = config?.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(buildUrl(path, config), {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    })

    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`
      try {
        const payload = await response.json() as {
          detail?: string | { code?: string; message?: string }
        }
        if (typeof payload.detail === 'string') {
          detail = payload.detail
        } else if (payload.detail?.message) {
          detail = payload.detail.code
            ? `${payload.detail.code}: ${payload.detail.message}`
            : payload.detail.message
        }
      } catch {
        // The status text remains the most useful fallback for non-JSON errors.
      }
      throw new Error(detail)
    }

    if (response.status === 204 || response.headers.get('Content-Length') === '0') {
      return {
        data: undefined as T,
        status: response.status,
      }
    }

    const text = await response.text()
    return {
      data: (text ? JSON.parse(text) : undefined) as T,
      status: response.status,
    }
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') {
      throw new Error(`请求超时（${timeoutMs}ms）：${path}`)
    }
    if (cause instanceof TypeError) {
      throw new Error(`无法连接后端服务：${path}`)
    }
    throw cause
  } finally {
    window.clearTimeout(timeoutId)
  }
}

async function requestBlob(
  path: string,
  init: RequestInit,
  config?: ApiRequestConfig,
): Promise<ApiBlobResponse> {
  const controller = new AbortController()
  const timeoutMs = config?.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(buildUrl(path, config), {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/zip',
        'Content-Type': 'application/json',
        ...init.headers,
      },
    })
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`
      try {
        const payload = await response.json() as {
          detail?: string | { code?: string; message?: string }
        }
        if (typeof payload.detail === 'string') detail = payload.detail
        else if (payload.detail?.message) {
          detail = payload.detail.code
            ? `${payload.detail.code}: ${payload.detail.message}`
            : payload.detail.message
        }
      } catch {
        // Keep the HTTP status fallback for non-JSON responses.
      }
      throw new Error(detail)
    }
    return { data: await response.blob(), status: response.status, headers: response.headers }
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') {
      throw new Error(`请求超时（${timeoutMs}ms）：${path}`)
    }
    if (cause instanceof TypeError) throw new Error(`无法连接后端服务：${path}`)
    throw cause
  } finally {
    window.clearTimeout(timeoutId)
  }
}

export const apiClient = {
  get<T>(path: string, config?: ApiRequestConfig): Promise<ApiResponse<T>> {
    return request<T>(path, undefined, config)
  },
  post<T>(path: string, payload: unknown, config?: ApiRequestConfig): Promise<ApiResponse<T>> {
    return request<T>(path, {
      method: 'POST',
      body: JSON.stringify(payload),
    }, config)
  },
  postBlob(path: string, payload: unknown, config?: ApiRequestConfig): Promise<ApiBlobResponse> {
    return requestBlob(path, {
      method: 'POST',
      body: JSON.stringify(payload),
    }, config)
  },
  delete<T>(path: string, config?: ApiRequestConfig): Promise<ApiResponse<T>> {
    return request<T>(path, { method: 'DELETE' }, config)
  },
}
