/** Types for external Agent run system. */

export interface AgentRun {
  id: string
  project_id: string
  source: string
  client_name: string | null
  title: string | null
  status: 'created' | 'running' | 'waiting_confirmation' | 'completed' | 'failed' | 'cancelled'
  current_step: string | null
  summary: string | null
  created_at: string
  updated_at: string | null
  completed_at: string | null
}

export interface AgentRunEvent {
  id: string
  run_id: string
  sequence: number
  event_type: string
  status: string
  message: string | null
  payload_json: string | null
  created_at: string
}

export interface AgentRunListResponse {
  items: AgentRun[]
  total: number
}

export interface AgentRunEventListResponse {
  items: AgentRunEvent[]
  total: number
}

/** Parsed event payload. */
export interface EventPayload {
  plan?: string[]
  step?: number
  detail?: string
  tool?: string
  args_summary?: string
  sources?: Array<{ source_type: string; source_id: string; title: string; reason: string }>
  content?: string
  chunk_index?: number
  content_type?: string
  summary?: string
  write_type?: string
  result_status?: string
  result_detail?: string
}
