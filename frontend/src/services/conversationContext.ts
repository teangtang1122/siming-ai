import { apiClient } from '../api/client'
import type {
  ConversationCheckpointDetail,
  ConversationCheckpointList,
  ConversationCheckpointSummary,
  ConversationContextState,
} from '../types/conversationContext'

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

const conversationContextBase = (projectId: string, conversationId: string) => (
  `/projects/${encodeURIComponent(projectId)}/ai/assistant/conversations/${encodeURIComponent(conversationId)}`
)

const creationConversationContextBase = (sessionId: string, conversationId: string) => (
  `/novel-creation/sessions/${encodeURIComponent(sessionId)}/conversations/${encodeURIComponent(conversationId)}`
)

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function normalizedState(candidate: Record<string, unknown>): ConversationContextState {
  const sourceRange = record(candidate.source_range) || {
    first_sequence: candidate.source_first_sequence,
    last_sequence: candidate.source_last_sequence,
    message_count: candidate.source_message_count,
    source_hash: candidate.source_hash,
    started_at: candidate.source_started_at,
    ended_at: candidate.source_ended_at,
  }
  return {
    ...candidate,
    source_range: sourceRange,
    original_history_tokens: candidate.original_history_tokens ?? candidate.original_tokens,
    active_history_tokens: candidate.active_history_tokens ?? candidate.active_tokens,
  } as unknown as ConversationContextState
}

function normalizedCheckpoint(candidate: Record<string, unknown>): ConversationCheckpointDetail {
  const normalized = normalizedState(candidate)
  return {
    ...candidate,
    ...normalized,
    id: String(candidate.id),
  } as unknown as ConversationCheckpointDetail
}

function normalizedCheckpointSummary(
  candidate: Record<string, unknown>,
): ConversationCheckpointSummary {
  const sourceRange = record(candidate.source_range) || {}
  return {
    ...candidate,
    source_range: sourceRange,
    id: String(candidate.id),
  } as unknown as ConversationCheckpointSummary
}

export function contextStateFromEvent(event: unknown): ConversationContextState | null {
  const root = record(event)
  if (!root) return null
  const data = record(root.data)
  const candidate = record(root.context_state)
    || record(root.state)
    || record(data?.context_state)
    || record(data?.state)
    || (typeof data?.status === 'string' ? data : null)
  return candidate && typeof candidate.status === 'string'
    ? normalizedState(candidate)
    : null
}

export function checkpointFromEvent(event: unknown): ConversationCheckpointDetail | null {
  const root = record(event)
  if (!root) return null
  const data = record(root.data)
  const candidate = record(root.checkpoint)
    || record(data?.checkpoint)
    || (typeof data?.id === 'string' && typeof data?.status === 'string' ? data : null)
  return candidate && typeof candidate.id === 'string' && typeof candidate.status === 'string'
    ? normalizedCheckpoint(candidate)
    : null
}

export function checkpointIdForState(
  state?: ConversationContextState | null,
  detail?: ConversationCheckpointDetail | null,
): string | null {
  if (state?.status === 'ready') {
    // A ready state without an active pointer means the conversation has not
    // been compacted (or its derived checkpoint was deleted).  An older
    // superseded/failed attempt must never be presented as active context.
    return state.active_checkpoint_id || null
  }
  return state?.latest_checkpoint_id || detail?.id || state?.active_checkpoint_id || null
}

export async function getConversationContextState(
  projectId: string,
  conversationId: string,
): Promise<ConversationContextState> {
  const response = await apiClient.get<ApiResponse<ConversationContextState>>(
    `${conversationContextBase(projectId, conversationId)}/context-state`,
  )
  const payload = record(response.data.data)
  if (!payload || typeof payload.status !== 'string') throw new Error('上下文状态响应不完整')
  return normalizedState(payload)
}

export async function getConversationCheckpoint(
  projectId: string,
  conversationId: string,
  checkpointId: string,
): Promise<ConversationCheckpointDetail> {
  const response = await apiClient.get<ApiResponse<ConversationCheckpointDetail>>(
    `${conversationContextBase(projectId, conversationId)}/checkpoints/${encodeURIComponent(checkpointId)}`,
  )
  const payload = record(response.data.data)
  if (!payload || typeof payload.id !== 'string' || typeof payload.status !== 'string') {
    throw new Error('checkpoint 详情响应不完整')
  }
  return normalizedCheckpoint(payload)
}

export async function getConversationCheckpoints(
  projectId: string,
  conversationId: string,
): Promise<ConversationCheckpointList> {
  const response = await apiClient.get<ApiResponse<ConversationCheckpointList>>(
    `${conversationContextBase(projectId, conversationId)}/checkpoints`,
  )
  const payload = record(response.data.data)
  if (!payload || !Array.isArray(payload.items)) throw new Error('checkpoint 列表响应不完整')
  return {
    items: payload.items
      .map(record)
      .filter((item): item is Record<string, unknown> => (
        Boolean(item && typeof item.id === 'string' && typeof item.status === 'string')
      ))
      .map(normalizedCheckpointSummary),
    total: Number(payload.total ?? payload.items.length),
  }
}

export async function getCreationConversationContextState(
  sessionId: string,
  conversationId: string,
): Promise<ConversationContextState> {
  const response = await apiClient.get<ApiResponse<ConversationContextState>>(
    `${creationConversationContextBase(sessionId, conversationId)}/context-state`,
  )
  const payload = record(response.data.data)
  if (!payload || typeof payload.status !== 'string') throw new Error('立项上下文状态响应不完整')
  return normalizedState(payload)
}

export async function getCreationConversationCheckpoint(
  sessionId: string,
  conversationId: string,
  checkpointId: string,
): Promise<ConversationCheckpointDetail> {
  const response = await apiClient.get<ApiResponse<ConversationCheckpointDetail>>(
    `${creationConversationContextBase(sessionId, conversationId)}/checkpoints/${encodeURIComponent(checkpointId)}`,
  )
  const payload = record(response.data.data)
  if (!payload || typeof payload.id !== 'string' || typeof payload.status !== 'string') {
    throw new Error('立项 checkpoint 详情响应不完整')
  }
  return normalizedCheckpoint(payload)
}

export async function rebuildConversationCheckpoint(
  projectId: string,
  conversationId: string,
): Promise<ConversationContextState> {
  const response = await apiClient.post<ApiResponse<ConversationContextState>>(
    `${conversationContextBase(projectId, conversationId)}/checkpoints/rebuild`,
  )
  const payload = record(response.data.data)
  if (!payload || typeof payload.status !== 'string') throw new Error('重建 checkpoint 响应不完整')
  return normalizedState(payload)
}

export async function cancelConversationCheckpoint(
  projectId: string,
  conversationId: string,
  checkpointId: string,
): Promise<ConversationContextState> {
  const response = await apiClient.post<ApiResponse<ConversationContextState>>(
    `${conversationContextBase(projectId, conversationId)}/checkpoints/${encodeURIComponent(checkpointId)}/cancel`,
  )
  const payload = record(response.data.data)
  if (!payload || typeof payload.status !== 'string') throw new Error('取消 checkpoint 响应不完整')
  return normalizedState(payload)
}

export async function deleteConversationCheckpoint(
  projectId: string,
  conversationId: string,
  checkpointId: string,
): Promise<ConversationContextState> {
  const response = await apiClient.delete<ApiResponse<ConversationContextState>>(
    `${conversationContextBase(projectId, conversationId)}/checkpoints/${encodeURIComponent(checkpointId)}`,
  )
  const payload = record(response.data.data)
  if (!payload || typeof payload.status !== 'string') throw new Error('删除 checkpoint 响应不完整')
  return normalizedState(payload)
}
