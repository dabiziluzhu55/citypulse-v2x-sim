import { apiClient } from './client'
import type {
  AlgorithmsResponse,
  SwitchAlgorithmRequest,
  SwitchAlgorithmResponse,
} from '../types/algorithm'

export async function fetchAlgorithms(): Promise<AlgorithmsResponse> {
  const { data } = await apiClient.get<AlgorithmsResponse>('/algorithms')
  return data
}

export async function switchRunAlgorithm(
  runId: string,
  payload: SwitchAlgorithmRequest,
): Promise<SwitchAlgorithmResponse> {
  const { data } = await apiClient.post<SwitchAlgorithmResponse>(
    `/runs/${runId}/algorithm`,
    payload,
  )
  return data
}
