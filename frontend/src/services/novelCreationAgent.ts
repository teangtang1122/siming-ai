import { apiClient } from '../api/client'

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface StartSessionData {
  session_id?: string
  session?: { id?: string }
}

export interface StartNovelCreationSessionInput {
  userBrief: string
  mode?: 'template' | 'internal_llm'
  form?: object
  creationMode?: 'author_led' | 'explore'
  authorBrief?: string
  authorOutline?: string
  lockedRequirements?: string[]
}

export interface NovelCreationSessionResult {
  id: string
  raw: unknown
}

export interface NovelCreationRunSummary {
  id: string
  session_id?: string
  stage: string
  status: string
  current_message?: string
  failure_class?: string
  next_action?: string
  operation_id?: string
  input_revision?: number
  input_snapshot_hash?: string
  model_source?: string
  attempt?: number
  result_mode?: 'model' | 'repaired' | 'deterministic_fallback'
  warning?: string
}

interface ConceptRunData {
  run: NovelCreationRunSummary
}

export interface CreationAgentTurnData {
  reply: string
  run?: Record<string, unknown> | null
  tool_results?: Array<Record<string, unknown>>
  write_count?: number
}

export async function startNovelCreationSession(input: StartNovelCreationSessionInput): Promise<NovelCreationSessionResult> {
  const response = await apiClient.post<ApiResponse<StartSessionData>>('/novel-creation/start', {
    mode: input.mode || 'template',
    user_brief: input.userBrief,
    creation_mode: input.creationMode || 'author_led',
    author_brief: input.authorBrief ?? input.userBrief,
    author_outline: input.authorOutline || '',
    locked_requirements: input.lockedRequirements || [],
    ...(input.form || {}),
  })
  const data = response.data.data
  const id = data.session_id || data.session?.id
  if (!id) throw new Error('新书立项会话未创建成功，请重试。')
  return { id, raw: data }
}

export async function runCreationAgentTurn(
  sessionId: string,
  message: string,
  model?: string,
  history: Array<{ role: string; content: string }> = [],
): Promise<CreationAgentTurnData> {
  const response = await apiClient.post<ApiResponse<CreationAgentTurnData>>('/novel-creation/agent-turn', {
    session_id: sessionId,
    message,
    model: model || null,
    history,
  }, { timeout: 0 })
  return response.data.data
}

/** Structured stage generation remains available to the full editor and Agent tools. */
export async function startNovelCreationConceptRun(
  sessionId: string,
  model?: string,
  expectedRevision?: number,
  operation: 'generate' | 'regenerate' | 'refine' = 'generate',
  instruction?: string,
) {
  const response = await apiClient.post<ApiResponse<ConceptRunData>>(`/novel-creation/sessions/${sessionId}/runs`, {
    stage: 'concepts',
    model,
    use_model: true,
    operation,
    ...(instruction ? { instruction } : {}),
    ...(expectedRevision != null ? { expected_revision: expectedRevision } : {}),
  })
  return response.data.data.run
}

/** Full structured editor remains a secondary view, not the conversation control plane. */
export function workbenchUrl(sessionId: string, runIdOrStage?: string, model?: string) {
  const params = new URLSearchParams({ session: sessionId })
  if (runIdOrStage) {
    if (runIdOrStage.startsWith('run-') || runIdOrStage.length > 20) params.set('run', runIdOrStage)
    else params.set('stage', runIdOrStage)
  }
  if (model) params.set('model', model)
  return `/novel-creation?${params.toString()}`
}
