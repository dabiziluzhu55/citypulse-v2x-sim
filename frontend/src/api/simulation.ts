import { apiClient } from './client'
import type {
  DisturbanceEventPayload,
  SimulationSnapshot,
  SimulationPlaybackResponse,
  StartSimulationRequest,
  StartSimulationResponse,
  StopSimulationResponse,
} from '../types/simulation'

const SIMULATION_START_TIMEOUT_MS = 90_000
const SIMULATION_CONTROL_TIMEOUT_MS = 30_000

export async function startSimulation(
  payload: StartSimulationRequest,
): Promise<StartSimulationResponse> {
  const { data } = await apiClient.post<StartSimulationResponse>(
    '/simulations',
    payload,
    { timeoutMs: SIMULATION_START_TIMEOUT_MS },
  )
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
    { timeoutMs: SIMULATION_CONTROL_TIMEOUT_MS },
  )
  return data
}

export async function pauseSimulation(sessionId: string): Promise<SimulationPlaybackResponse> {
  const { data } = await apiClient.post<SimulationPlaybackResponse>(
    `/simulations/${sessionId}/pause`,
    {},
    { timeoutMs: SIMULATION_CONTROL_TIMEOUT_MS },
  )
  return data
}

export async function resumeSimulation(sessionId: string): Promise<SimulationPlaybackResponse> {
  const { data } = await apiClient.post<SimulationPlaybackResponse>(
    `/simulations/${sessionId}/resume`,
    {},
    { timeoutMs: SIMULATION_CONTROL_TIMEOUT_MS },
  )
  return data
}

export async function setSimulationPlaybackSpeed(
  sessionId: string,
  playbackSpeed: number,
): Promise<SimulationPlaybackResponse> {
  const { data } = await apiClient.post<SimulationPlaybackResponse>(
    `/simulations/${sessionId}/playback-speed`,
    { playback_speed: playbackSpeed },
    { timeoutMs: SIMULATION_CONTROL_TIMEOUT_MS },
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
    { timeoutMs: SIMULATION_CONTROL_TIMEOUT_MS },
  )
  return data
}

export async function cancelSimulationEvent(
  sessionId: string,
  eventId: string,
): Promise<void> {
  await apiClient.delete(
    `/simulations/${sessionId}/events/${eventId}`,
    { timeoutMs: SIMULATION_CONTROL_TIMEOUT_MS },
  )
}
