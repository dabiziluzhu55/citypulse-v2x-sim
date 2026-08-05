import type { SimulationState } from '../types/simulation'

export const ACTIVE_SIMULATION_STATES: SimulationState[] = [
  'QUEUED',
  'STARTING',
  'RUNNING',
  'PAUSED',
  'STOPPING',
]

export function isActiveSimulationState(state: SimulationState | null | undefined): boolean {
  return !!state && ACTIVE_SIMULATION_STATES.includes(state)
}

export function shouldAutoPresentSimulation(state: SimulationState | null | undefined): boolean {
  return !!state && ['STARTING', 'RUNNING', 'PAUSED', 'STOPPING'].includes(state)
}

export function canPauseSimulation(state: SimulationState | null | undefined): boolean {
  return state === 'RUNNING'
}

export function canResumeSimulation(state: SimulationState | null | undefined): boolean {
  return state === 'PAUSED'
}

export function simulationStateLabel(state: SimulationState | null): string | null {
  return state === 'QUEUED' ? '排队中' : state
}
