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

  it('surfaces partial success and waiting confirmation explicitly', () => {
    const partial = assistantOutcomeToRunLog(
      { ...createEmptyWorkspaceResponse(), reply: '正文已保存', outcome: 'partial_success' },
      'project',
    )
    const waiting = assistantOutcomeToRunLog(
      { ...createEmptyWorkspaceResponse(), reply: '请确认', outcome: 'waiting_user' },
      'project',
    )

    expect(partial.status).toBe('blocked')
    expect(partial.message).toContain('部分')
    expect(waiting.status).toBe('blocked')
    expect(waiting.message).toContain('等待你的确认')
  })

  it('does not turn an unknown empty outcome into generic completion', () => {
    const log = assistantOutcomeToRunLog(
      { ...createEmptyWorkspaceResponse(), reply: '' },
      'project',
    )

    expect(log.status).toBe('skipped')
    expect(log.message).toContain('没有返回')
  })
})
