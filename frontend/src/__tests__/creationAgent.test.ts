import { describe, expect, it } from 'vitest'

import {
  extractCreationAgentErrorDetail,
  formatSystemAssistantError,
} from '../utils/creationAgent'

function normalizedApiError(status: number, detail: Record<string, unknown>) {
  const error = new Error(String(detail.message || '请求失败')) as Error & {
    response?: {
      status: number
      data: { detail: Record<string, unknown> }
    }
  }
  error.response = { status, data: { detail } }
  return error
}

describe('creation agent structured errors', () => {
  it('keeps runtime quota diagnostics after apiClient normalizes Axios errors', () => {
    const error = normalizedApiError(422, {
      message: 'Free usage exceeded, retrying in 9h',
      failure_class: 'quota_or_rate_limit',
      next_action: '切换有额度的模型后重试。',
      runtime: {
        effective_model: 'opencode_cli:free-model',
        provider: 'opencode_cli',
        model_source: 'global_default',
        quota_status: 'exhausted_or_limited',
      },
    })

    expect(extractCreationAgentErrorDetail(error)).toMatchObject({
      failure_class: 'quota_or_rate_limit',
      runtime: {
        effective_model: 'opencode_cli:free-model',
        quota_status: 'exhausted_or_limited',
      },
    })
    expect(formatSystemAssistantError(error)).toContain('Free usage exceeded')
    expect(formatSystemAssistantError(error)).toContain('切换有额度的模型后重试。')
  })

  it('still applies status-specific guidance to normalized authentication errors', () => {
    const error = normalizedApiError(401, { message: 'invalid token' })

    expect(formatSystemAssistantError(error)).toBe('当前模型鉴权失败，请到设置重新登录或检查 API Key。')
  })
})