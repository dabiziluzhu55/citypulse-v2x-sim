import { apiClient } from './client'
import type { TrafficStateSnapshot } from '../types/traffic'

export async function fetchTrafficState(runId: string): Promise<TrafficStateSnapshot> {
  const { data } = await apiClient.get<TrafficStateSnapshot>(`/runs/${runId}/traffic-state`)
  return data
}
