import { apiClient } from './client'
import type { StartSimulationRequest } from '../types/simulation'

export async function exportScenarioArchive(
  payload: StartSimulationRequest,
): Promise<{ blob: Blob; filename: string | null }> {
  const { data, headers } = await apiClient.postBlob('/scenarios/export', payload, {
    timeoutMs: 60_000,
  })
  const disposition = headers.get('Content-Disposition') ?? ''
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1]
  return {
    blob: data,
    filename: encoded ? decodeURIComponent(encoded) : plain ?? null,
  }
}
