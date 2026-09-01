import { apiClient } from './client.ts'
import type { CopilotChatRequest, CopilotChatResponse } from '../types/copilot'

const COPILOT_REQUEST_TIMEOUT_MS = 90_000

export async function chatWithCopilot(
  sessionId: string,
  payload: CopilotChatRequest,
  signal?: AbortSignal,
): Promise<CopilotChatResponse> {
  const normalizedSessionId = sessionId.trim()
  if (!normalizedSessionId) throw new Error('请先启动仿真，再向交通 Copilot 提问')

  const { data } = await apiClient.post<CopilotChatResponse>(
    `/simulations/${encodeURIComponent(normalizedSessionId)}/copilot/chat`,
    payload,
    { timeoutMs: COPILOT_REQUEST_TIMEOUT_MS, signal },
  )
  return data
}
