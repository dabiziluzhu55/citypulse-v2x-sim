import { apiClient } from './client.ts'
import type { CopilotChatRequest, CopilotChatResponse } from '../types/copilot'

const COPILOT_REQUEST_TIMEOUT_MS = 90_000

export async function chatWithCopilot(
  payload: CopilotChatRequest,
  sessionId?: string | null,
  signal?: AbortSignal,
): Promise<CopilotChatResponse> {
  const normalizedSessionId = sessionId?.trim() || undefined
  const { data } = await apiClient.post<CopilotChatResponse>(
    '/copilot/chat',
    {
      ...payload,
      session_id: normalizedSessionId,
    },
    { timeoutMs: COPILOT_REQUEST_TIMEOUT_MS, signal },
  )
  return data
}
