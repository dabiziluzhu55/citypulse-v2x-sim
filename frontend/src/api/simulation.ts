import { apiClient } from './client'
import type {
  DisturbanceEventPayload,
  SimulationSnapshot,
  StartSimulationRequest,
  StartSimulationResponse,
  StopSimulationResponse,
} from '../types/simulation'

export async function startSimulation(
  payload: StartSimulationRequest,
): Promise<StartSimulationResponse> {
  const { data } = await apiClient.post<StartSimulationResponse>('/simulations', payload)
  return data
}

export async function fetchSimulationStatus(sessionId: string): Promise<SimulationSnapshot> {
  const { data } = await apiClient.get<SimulationSnapshot>(`/simulations/${sessionId}`)
  return data
}

export async function stopSimulation(sessionId: string): Promise<StopSimulationResponse> {
  const { data } = await apiClient.post<StopSimulationResponse>(
    `/simulations/${sessionId}/stop`,
    {},
  )
  return data
}

export async function addSimulationEvent(
  sessionId: string,
  event: DisturbanceEventPayload,
): Promise<{ event_id: string }> {
  const { data } = await apiClient.post<{ event_id: string }>(
    `/simulations/${sessionId}/events`,
    event,
  )
  return data
}

export async function cancelSimulationEvent(
  sessionId: string,
  eventId: string,
): Promise<void> {
  await apiClient.delete(`/simulations/${sessionId}/events/${eventId}`)
}
