import { apiClient } from './client'
import type { EventsResponse, PredictionResponse } from '../types/events'

export async function fetchRunEvents(runId: string): Promise<EventsResponse> {
  const { data } = await apiClient.get<EventsResponse>(`/runs/${runId}/events`)
  return data
}

export async function fetchRunPrediction(
  runId: string,
  target: string,
  horizon = 300,
): Promise<PredictionResponse> {
  const { data } = await apiClient.get<PredictionResponse>(`/runs/${runId}/prediction`, {
    params: { target, horizon },
  })
  return data
}
