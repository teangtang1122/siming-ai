import { describe, expect, it } from 'vitest'
import {
  formatSystemAssistantError,
  formatNovelInterviewError,
  isNovelInterviewRetryIntent,
  NOVEL_INTERVIEW_THINKING,
} from '../utils/novelInterview'

describe('novel interview helpers', () => {
  it('recognizes retry messages without treating them as story facts', () => {
    expect(isNovelInterviewRetryIntent('？')).toBe(true)
    expect(isNovelInterviewRetryIntent('继续')).toBe(true)
    expect(isNovelInterviewRetryIntent('主角决定逃离组织')).toBe(false)
  })

  it('preserves the concrete model error and explains recovery', () => {
    const text = formatNovelInterviewError(new Error('本机 CLI 提供方额度已耗尽'))
    expect(text).toContain('额度已耗尽')
    expect(text).toContain('本轮回答已保留')
    expect(text).toContain('直接生成')
  })

  it('keeps ordinary assistant recovery separate from the novel interview', () => {
    const text = formatSystemAssistantError(new Error('模型没有返回文字'))
    expect(text).toContain('模型没有返回文字')
    expect(text).toContain('系统设置')
    expect(text).not.toContain('直接生成')
  })

  it('describes a model-owned next-step decision', () => {
    expect(NOVEL_INTERVIEW_THINKING).toContain('下一问')
    expect(NOVEL_INTERVIEW_THINKING).toContain('方案生成')
  })
})
