/* Shared types for the assistant chat system. */
export type {
  ConversationCheckpointAuthorQuote,
  ConversationCapacityAssurance,
  ConversationCheckpointDetail,
  ConversationCheckpointLedgerEntry,
  ConversationCheckpointModelBinding,
  ConversationCheckpointResourceRef,
  ConversationCheckpointSemanticNavigation,
  ConversationCheckpointSourceRange,
  ConversationCheckpointStatus,
  ConversationContextState,
} from '../../types/conversationContext'

export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface WorkspaceToolLog {
  tool?: string
  status?: string
  detail?: string
  stepId?: string
  data?: Record<string, unknown>
}

export interface WorkspaceAction {
  tool?: string
  arguments?: Record<string, unknown>
}

export type WorkspaceAssistantOutcome =
  | 'completed_with_reply'
  | 'completed_with_tools'
  | 'partial_success'
  | 'empty_response'
  | 'skipped_preflight'
  | 'waiting_user'
  | 'blocked'
  | 'failed'
  | string

export interface WorkspaceAssistantConversation {
  id: string
  project_id: string
  title: string
  scope?: string
  model?: string | null
  message_count?: number
  created_at?: string | null
  updated_at?: string | null
}

export interface WorkspaceAssistantResponse {
  reply: string
  reasoning_content?: string
  outcome?: WorkspaceAssistantOutcome
  actions?: WorkspaceAction[]
  applied_actions?: WorkspaceToolLog[]
  tool_logs: WorkspaceToolLog[]
  run?: WorkspaceAssistantRun
  scope?: string
  model?: string | null
  usage?: unknown
  message?: WorkspacePersistedMessage
  conversation?: WorkspaceAssistantConversation
}

export interface WorkspaceMessageNavigationAction {
  label: string
  to: string
}

export interface WorkspaceAssistantMessage {
  id?: string
  conversation_id?: string
  sequence_no?: number
  role: 'user' | 'assistant'
  content: string
  reasoning_content?: string
  status?: string
  created_at?: string
  updated_at?: string
  data?: WorkspaceAssistantResponse
  navigation_action?: WorkspaceMessageNavigationAction
}

export interface WorkspacePersistedMessage {
  id: string
  conversation_id: string
  sequence_no?: number
  role: 'user' | 'assistant'
  content: string
  payload?: WorkspaceAssistantResponse | null
  status: string
  created_at?: string | null
  updated_at?: string | null
}

export interface WorkspaceRunLog {
  key: string
  tool?: string
  status?: string
  message: string
  stepId?: string
  attemptNo?: number
  retryOfStepId?: string | null
  resolvedStepId?: string | null
  canRetry?: boolean
  retryBlockReason?: string | null
}

export type WorkspaceAssistantRunStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'error'
  | 'aborted'
  | 'cancelled'
  | 'interrupted'

export interface WorkspaceAssistantRun {
  id: string
  operation_id?: string | null
  status: WorkspaceAssistantRunStatus
  phase?: string | null
  model?: string | null
  error?: string | null
  current_iteration?: number
  created_at?: string | null
  updated_at?: string | null
  completed_at?: string | null
}

export interface WorkspaceAssistantRunStep {
  id: string
  run_id: string
  step_type?: string | null
  tool?: string | null
  status?: string | null
  detail?: string | null
  error?: string | null
  can_retry?: boolean
  retry_block_reason?: string | null
}

export interface WorkspaceAssistantRunDetail {
  run: WorkspaceAssistantRun
  assistant_message?: WorkspacePersistedMessage | null
  steps: WorkspaceAssistantRunStep[]
}

export interface WorkspaceAssistantModelOption {
  value: string
  label: string
}

export interface WorkspaceAssistantChatProps {
  projectId: string
  selectedText?: string
  selectedTextChapterId?: string | null
  defaultModel?: string
  modelOptions?: WorkspaceAssistantModelOption[]
  modelsLoading?: boolean
  onTaskModelChange?: (model?: string) => Promise<unknown>
  onManageModels?: () => void
  onApplied?: () => void | Promise<void>
}

export interface StepDetail {
  id: string
  tool?: string
  request?: unknown
  result?: unknown
  error?: string
  attempt_no?: number
}
