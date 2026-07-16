/* Shared types for the assistant chat system. */

export type WorkspaceAssistantScope = 'outline' | 'characters' | 'worldbuilding' | 'project'
export type WorkspaceAssistantMode = 'fast' | 'quality'

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

export interface WorkspaceAssistantMessage {
  id?: string
  conversation_id?: string
  role: 'user' | 'assistant'
  content: string
  status?: string
  created_at?: string
  updated_at?: string
  data?: WorkspaceAssistantResponse
}

export interface WorkspacePersistedMessage {
  id: string
  conversation_id: string
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
}

export interface WorkspaceAssistantRun {
  id: string
  status: string
  phase?: string | null
  current_iteration?: number
  created_at?: string | null
  updated_at?: string | null
}

export interface WorkspaceAssistantRunStep {
  id: string
  run_id: string
  step_type?: string | null
  tool?: string | null
  status?: string | null
  detail?: string | null
  error?: string | null
}

export interface WorkspaceAssistantRunDetail {
  run: WorkspaceAssistantRun
  steps: WorkspaceAssistantRunStep[]
}

export interface WorkspaceAssistantChatProps {
  projectId: string
  scope: WorkspaceAssistantScope
  selectedOutlineNodeId?: string | null
  selectedCharacterId?: string | null
  selectedText?: string
  selectedTextChapterId?: string | null
  defaultModel?: string
  onApplied?: () => void | Promise<void>
}

export interface SkillMatch {
  name: string
  description?: string
  truncated?: boolean
  warnings?: string[]
  recommended_tools?: string[]
  injected?: boolean
}

export interface StepDetail {
  id: string
  tool?: string
  request?: unknown
  result?: unknown
  error?: string
  attempt_no?: number
}
