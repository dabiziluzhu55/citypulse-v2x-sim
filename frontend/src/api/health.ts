import { apiClient } from './client'
import type { HealthResponse } from '../types/health'

export interface HealthResult {
  httpStatus: number
  payload: HealthResponse
  ready: boolean
}

export async function fetchHealth(): Promise<HealthResult> {
  const { data, status } = await apiClient.get<HealthResponse>('/health')
  return {
    httpStatus: status,
    payload: data,
    ready: status === 200 && data.status === 'ok',
  }
}
