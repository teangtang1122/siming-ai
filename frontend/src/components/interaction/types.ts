export type LifecycleStatus =
  | 'draft'
  | 'queued'
  | 'running'
  | 'waiting_user'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export type OperationOutcome =
  | 'completed_with_reply'
  | 'completed_with_tools'
  | 'partial_success'
  | 'empty_response'
  | 'skipped_preflight'
  | 'waiting_user'
  | 'blocked'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export type SaveStatus = 'saved' | 'dirty' | 'saving' | 'error'

export interface OperationAttention {
  kind?: 'confirmation' | 'recovery' | 'review' | string
  title?: string
  message?: string
  action_label?: string
  action_url?: string
  blocking?: boolean
}
export interface OperationResult {
  outcome?: OperationOutcome | string
  summary?: string
  completed?: string[]
  incomplete?: string[]
  warnings?: string[]
}
