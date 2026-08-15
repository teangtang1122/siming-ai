import axios from 'axios'

export type CreationAgentModelSource = 'conversation_override' | 'global_default' | 'task_setting' | 'task_setting_fallback' | 'unconfigured' | 'unknown'

export interface CreationAgentRuntime {
  effective_model?: string
  provider?: string
  model_source?: CreationAgentModelSource
  tool_mode?: string
  timeout_seconds?: number
  quota_status?: 'unknown' | 'available' | 'exhausted_or_limited'
  failure_class?: string
  next_action?: string
}

export const defaultCreationAgentRuntime = (
  model?: string,
  source: CreationAgentModelSource = 'unknown',
): CreationAgentRuntime => {
  const [provider] = String(model || '').split(':')
  return {
    effective_model: model || undefined,
    provider: provider || undefined,
    model_source: source,
    tool_mode: 'creation_agent_tools',
    timeout_seconds: 0,
    quota_status: 'unknown',
  }
}

export const extractCreationAgentErrorDetail = (error: unknown): Record<string, unknown> => {
  if (!axios.isAxiosError(error)) return {}
  const detail = error.response?.data?.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) return detail as Record<string, unknown>
  return {}
}

function sharedErrorMessage(error: unknown): string {
  if (!axios.isAxiosError(error)) {
    return error instanceof Error ? error.message : '模型调用失败，请重试。'
  }
  const status = error.response?.status
  const detail = error.response?.data?.detail
  const detailMessage = typeof detail === 'string'
    ? detail
    : detail && typeof detail === 'object'
      ? String((detail as any).message || (detail as any).detail || '')
      : ''
  if (status === 401 || status === 403) return '当前模型鉴权失败，请到设置重新登录或检查 API Key。'
  if (status === 404) return detailMessage || '当前模型或接口不可用，请检查模型配置。'
  if (status === 429) return '当前模型额度不足或请求过于频繁，请稍后重试或切换模型。'
  if (status && status >= 500) return '模型服务暂时不可用，请稍后重试。'
  return detailMessage || error.message || '模型调用失败，请重试。'
}

export const formatCreationAgentError = (error: unknown): string => {
  const detail = extractCreationAgentErrorDetail(error)
  const failure = String(detail.failure_class || '')
  const nextAction = String(detail.next_action || '')
  const base = sharedErrorMessage(error)
  if (nextAction && !base.includes(nextAction)) return `${base}\n${nextAction}`
  if (failure === 'quota_or_rate_limit' && !/额度|频繁/.test(base)) return `${base}\n请等待额度恢复或切换模型。`
  return base
}

export const formatSystemAssistantError = (error: unknown): string => formatCreationAgentError(error)
