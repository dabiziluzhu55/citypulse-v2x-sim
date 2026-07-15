import { apiClient } from './client'
import type {
  CreateScenarioRequest,
  CreateScenarioResponse,
  ScenarioTemplatesResponse,
} from '../types/scenario'

export async function fetchScenarioTemplates(): Promise<ScenarioTemplatesResponse> {
  const { data } = await apiClient.get<ScenarioTemplatesResponse>('/scenario-templates')
  return data
}

export async function createScenario(
  payload: CreateScenarioRequest,
): Promise<CreateScenarioResponse> {
  const { data } = await apiClient.post<CreateScenarioResponse>('/scenarios', payload)
  return data
}
