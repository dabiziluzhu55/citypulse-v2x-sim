import { apiClient } from './client'
import type { CollaborationStateSnapshot } from '../types/collaboration'

export async function fetchCollaborationState(
  runId: string,
): Promise<CollaborationStateSnapshot> {
  const { data } = await apiClient.get<CollaborationStateSnapshot>(
    `/runs/${runId}/collaboration-state`,
  )
  return data
}
