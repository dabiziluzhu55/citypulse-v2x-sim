export interface ApiResponse<T> {
  data: T
  status: number
}

export interface ApiRequestConfig {
  params?: Record<string, string | number | boolean | null | undefined>
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')

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
  const response = await fetch(buildUrl(path, config), {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const payload = await response.json() as { detail?: string }
      detail = payload.detail || detail
    } catch {
      // The status text remains the most useful fallback for non-JSON errors.
    }
    throw new Error(detail)
  }

  return {
    data: await response.json() as T,
    status: response.status,
  }
}

export const apiClient = {
  get<T>(path: string, config?: ApiRequestConfig): Promise<ApiResponse<T>> {
    return request<T>(path, undefined, config)
  },
  post<T>(path: string, payload: unknown): Promise<ApiResponse<T>> {
    return request<T>(path, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },
}
