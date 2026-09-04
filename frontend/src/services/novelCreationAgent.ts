import { apiClient } from '../api/client'
import type { AssistantReferenceContext } from '../types/assistantReferenceContext'

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
  mode?: 'internal_llm' | 'external_agent'
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
  result_mode?: 'model' | 'repaired'
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
  created_project_id?: string | null
  message_status?: 'completed' | 'running' | 'error'
  conversation_id?: string
  assistant_message_id?: string
  turn_persisted?: boolean
}

export type CreationAgentTurnEventType =
  | 'turn_started'
  | 'model_step_started'
  | 'tool_categories_changed'
  | 'tool_started'
  | 'tool_completed'
  | 'conversation_context'
  | 'conversation_checkpoint'
  | 'reply_delta'
  | 'heartbeat'
  | 'complete'
  | 'error'
  | 'cancelled'

export interface CreationAgentTurnEvent {
  type: CreationAgentTurnEventType
  client_turn_id: string
  sequence: number
  message: string
  data?: Record<string, unknown>
}

export class CreationAgentTurnError extends Error {
  readonly detail: Record<string, unknown>
  readonly status?: number

  constructor(message: string, detail: Record<string, unknown> = {}, status?: number) {
    super(message)
    this.name = 'CreationAgentTurnError'
    this.detail = detail
    this.status = status
  }
}

function createClientTurnId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  const bytes = new Uint8Array(16)
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256)
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export async function startNovelCreationSession(input: StartNovelCreationSessionInput): Promise<NovelCreationSessionResult> {
  const response = await apiClient.post<ApiResponse<StartSessionData>>('/novel-creation/start', {
    mode: input.mode || 'internal_llm',
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
  options: {
    conversationId?: string
    assistantMessageId?: string
    localCliReadPaths?: string[]
    referenceContext?: AssistantReferenceContext
    clientTurnId?: string
    signal?: AbortSignal
    onEvent?: (event: CreationAgentTurnEvent) => void
  } = {},
): Promise<CreationAgentTurnData> {
  const clientTurnId = options.clientTurnId || createClientTurnId()
  let afterSequence = 0
  let lastError: Error | null = null

  for (let attempt = 0; attempt < 3; attempt += 1) {
    let response: Response
    try {
      response = await fetch('/api/v1/novel-creation/agent-turn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({
          session_id: sessionId,
          message,
          model: model || null,
          client_turn_id: clientTurnId,
          after_sequence: afterSequence,
          conversation_id: options.conversationId,
          assistant_message_id: options.assistantMessageId,
          reference_context: options.referenceContext,
          local_cli_read_permission_grant: options.localCliReadPaths?.length ? 'read_once' : 'none',
          local_cli_read_paths: options.localCliReadPaths || [],
        }),
        signal: options.signal,
      })
    } catch (error) {
      if (options.signal?.aborted || (error instanceof DOMException && error.name === 'AbortError')) throw error
      lastError = error instanceof Error ? error : new Error('立项助手连接失败')
      continue
    }
    if (!response.ok || !response.body) {
      if (response.status === 401 && typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('siming:gateway-unauthorized'))
      }
      const payload = await response.json().catch(() => null) as {
        detail?: string | ({ message?: string } & Record<string, unknown>)
        message?: string
      } | null
      const detail = typeof payload?.detail === 'string' ? payload.detail : payload?.detail?.message
      const detailData = payload?.detail && typeof payload.detail === 'object' ? payload.detail : {}
      throw new CreationAgentTurnError(
        detail || payload?.message || `立项助手请求失败（HTTP ${response.status}）`,
        detailData,
        response.status,
      )
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let terminal: CreationAgentTurnData | null = null
    let terminalEventError = false
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const frames = buffer.split(/\r?\n\r?\n/)
        buffer = frames.pop() || ''
        for (const frame of frames) {
          const data = frame
            .split(/\r?\n/)
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.replace(/^data:\s?/, ''))
            .join('\n')
          if (!data || data === '[DONE]') continue
          let event: CreationAgentTurnEvent
          try {
            event = JSON.parse(data) as CreationAgentTurnEvent
          } catch {
            continue
          }
          afterSequence = Math.max(afterSequence, Number(event.sequence || 0))
          options.onEvent?.(event)
          if (event.type === 'complete') {
            if (typeof event.data?.reply !== 'string') {
              terminalEventError = true
              throw new Error('立项助手完成事件缺少最终回复')
            }
            terminal = event.data as unknown as CreationAgentTurnData
          }
          if (event.type === 'error') {
            terminalEventError = true
            throw new CreationAgentTurnError(event.message || '立项助手处理失败', event.data || {})
          }
          if (event.type === 'cancelled') throw new DOMException(event.message || '本轮已取消', 'AbortError')
        }
      }
      if (terminal) return terminal
      lastError = new Error('立项助手流连接提前结束，正在恢复…')
    } catch (error) {
      if (options.signal?.aborted || (error instanceof DOMException && error.name === 'AbortError')) throw error
      if (terminalEventError) throw error
      lastError = error instanceof Error ? error : new Error('立项助手流连接中断')
    } finally {
      reader.releaseLock()
    }
  }
  throw lastError || new Error('立项助手流连接中断')
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
