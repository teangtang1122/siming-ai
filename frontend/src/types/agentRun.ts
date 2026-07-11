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
  prompt_pack_version?: string
  prompt_pack_id?: string
  model_source?: string
  tool_mode?: string
  failure_class?: string
  checkpoint_id?: string
  storage_target?: string
  next_action?: string
  storage_health?: {
    storage_target?: string
    orphan_chapter_files?: Array<{
      path: string
      id?: string | null
      title?: string | null
      word_count?: number | null
      modified_at?: string | null
    }>
    orphan_chapter_file_count?: number
    next_action?: string | null
    warning?: string | null
  }
  orphan_chapter_files?: Array<{ path: string; title?: string | null; word_count?: number | null }>
  validation?: Record<string, unknown>
  data?: Record<string, unknown>
}
