import type { components } from '../api/generated/schema'

type GeneratedContextState = components['schemas']['ConversationContextStateResponse']
type GeneratedCheckpointDetail = components['schemas']['ConversationCheckpointDetailResponse']
type GeneratedCheckpointSummary = components['schemas']['ConversationCheckpointSummaryResponse']

export type ConversationCheckpointStatus =
  | GeneratedContextState['status']
  | GeneratedCheckpointDetail['status']

export type ConversationCapacityAssurance = GeneratedContextState['capacity_assurance']

export interface ConversationCheckpointSourceRange {
  first_sequence?: number | null
  last_sequence?: number | null
  message_count?: number | null
  source_hash?: string | null
  started_at?: string | null
  ended_at?: string | null
}

export interface ConversationCheckpointAuthorQuote {
  message_id: string
  start_char?: number | null
  end_char?: number | null
  exact_quote: string
  quote_sha256?: string | null
  purpose?: string | null
  sequence?: number | null
  created_at?: string | null
  superseded?: boolean
}

export interface ConversationCheckpointResourceRef {
  type?: string | null
  id: string
  revision?: number | string | null
}

export interface ConversationCheckpointLedgerEntry {
  run_id?: string | null
  step_id?: string | null
  tool?: string | null
  status?: string | null
  detail?: string | null
  resource_refs?: ConversationCheckpointResourceRef[]
  error_code?: string | null
  created_at?: string | null
}

export interface ConversationCheckpointSemanticNavigation {
  authority?: 'non_authoritative_navigation' | string
  current_objectives?: string[]
  resolved_decisions?: string[]
  superseded_directions?: string[]
  unresolved_questions?: string[]
  next_context_needed?: string[]
}

export interface ConversationCheckpointModelBinding {
  provider?: string | null
  model?: string | null
  display_name?: string | null
}

export interface ConversationContextState {
  status: ConversationCheckpointStatus
  policy_version?: GeneratedContextState['policy_version'] | null
  schema_version?: number | string | null
  active_checkpoint_id?: string | null
  latest_checkpoint_id?: string | null
  source_message_count?: number | null
  source_range?: ConversationCheckpointSourceRange | null
  recent_exact_turn_count?: number | null
  original_history_tokens?: number | null
  active_history_tokens?: number | null
  checkpoint_tokens?: number | null
  trigger?: string | null
  capacity_assurance?: ConversationCapacityAssurance | null
  provider?: string | null
  model?: string | null
  model_binding?: ConversationCheckpointModelBinding | null
  warnings?: string[]
  error_code?: string | null
  error_detail?: string | null
  retryable?: boolean
  created_at?: string | null
  updated_at?: string | null
  completed_at?: string | null
}

export interface ConversationCheckpointDetail extends ConversationContextState {
  id: string
  schema?: string | null
  scope?: GeneratedCheckpointDetail['scope'] | null
  semantic_navigation?: ConversationCheckpointSemanticNavigation | null
  author_quotes?: ConversationCheckpointAuthorQuote[]
  execution_ledger?: ConversationCheckpointLedgerEntry[]
  project_refs?: ConversationCheckpointResourceRef[]
}

export interface ConversationCheckpointSummary {
  id: string
  status: GeneratedCheckpointSummary['status']
  policy_version: number
  schema_version: string
  scope: GeneratedCheckpointSummary['scope']
  source_range: ConversationCheckpointSourceRange
  source_message_count: number
  original_history_tokens?: number | null
  checkpoint_tokens?: number | null
  model_binding?: ConversationCheckpointModelBinding | null
  warnings?: string[]
  error_code?: string | null
  error_detail?: string | null
  retryable: boolean
  created_at?: string | null
  updated_at?: string | null
  completed_at?: string | null
}

export interface ConversationCheckpointList {
  items: ConversationCheckpointSummary[]
  total: number
}
