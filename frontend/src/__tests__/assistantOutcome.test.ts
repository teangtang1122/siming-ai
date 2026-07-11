import { describe, expect, it } from 'vitest'

import { assistantOutcomeToRunLog, createEmptyWorkspaceResponse } from '../components/assistant'

describe('assistantOutcomeToRunLog', () => {
  it('distinguishes an empty model response from a normal completion', () => {
    const log = assistantOutcomeToRunLog(
      { ...createEmptyWorkspaceResponse(), reply: '', outcome: 'empty_response' },
      'project',
    )

    expect(log.status).toBe('skipped')
    expect(log.message).toContain('没有返回文字')
  })

  it('shows tool completions as tool operations, not generic completion', () => {
    const log = assistantOutcomeToRunLog(
      { ...createEmptyWorkspaceResponse(), reply: '已创建章节', outcome: 'completed_with_tools' },
      'project',
    )

    expect(log.status).toBe('ok')
    expect(log.message).toContain('工具操作')
  })

  it('surfaces blocked turns explicitly', () => {
    const log = assistantOutcomeToRunLog(
      { ...createEmptyWorkspaceResponse(), reply: '需要确认', outcome: 'blocked' },
      'project',
    )

    expect(log.status).toBe('blocked')
    expect(log.message).toContain('阻塞')
  })
})
