import { apiClient } from './client'
import type { RunOverview } from '../types/overview'

export async function fetchRunOverview(runId: string): Promise<RunOverview> {
  const { data } = await apiClient.get<RunOverview>(`/runs/${runId}/overview`)
  return data
}
