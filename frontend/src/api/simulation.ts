import { apiClient } from './client'
import type {
  ControlRunRequest,
  ControlRunResponse,
  RunStatus,
  StartRunRequest,
  StartRunResponse,
} from '../types/simulation'

export async function startRun(payload: StartRunRequest): Promise<StartRunResponse> {
  const { data } = await apiClient.post<StartRunResponse>('/runs', payload)
  return data
}

export async function controlRun(
  runId: string,
  payload: ControlRunRequest,
): Promise<ControlRunResponse> {
  const { data } = await apiClient.post<ControlRunResponse>(`/runs/${runId}/control`, payload)
  return data
}

export async function fetchRunStatus(runId: string): Promise<RunStatus> {
  const { data } = await apiClient.get<RunStatus>(`/runs/${runId}/status`)
  return data
}
