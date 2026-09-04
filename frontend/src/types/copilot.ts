export type CopilotMessageRole = 'user' | 'assistant'

export interface CopilotHistoryMessage {
  role: CopilotMessageRole
  content: string
}

export interface CopilotChatRequest {
  message: string
  history?: CopilotHistoryMessage[]
  active_event_id?: string | null
  active_scope?: string | null
}

export interface CopilotToolCall {
  call_id: string
  name: string
  arguments: Record<string, unknown> | string
  result: Record<string, unknown> | null
  error: Record<string, unknown> | null
}

export interface CopilotChatResponse {
  session_id: string
  answer: string
  rounds: number
  tool_calls: CopilotToolCall[]
  model: string | null
  usage: Record<string, unknown>
  latency_ms: number | null
}
