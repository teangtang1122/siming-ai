import { describe, expect, it } from 'vitest'
import type { OperationRun } from '../shared/api/contracts'
import {
  desktopPetToneForState,
  deriveDesktopPetPresentation,
} from '../features/desktopPet/state'

const NOW = Date.parse('2026-08-26T10:00:00Z')

function operation(
  status: OperationRun['status'],
  overrides: Partial<OperationRun> = {},
): OperationRun {
  return {
    id: `operation-${status}`,
    source_kind: 'workspace_agent',
    source_id: 'source-1',
    project_id: 'project-1',
    title: '测试任务',
    status,
    health_status: 'active',
    outcome: null,
    attention: null,
    attention_read_at: null,
    result: null,
    result_summary: null,
    phase: null,
    current_message: null,
    progress: { mode: 'indeterminate', current: null, total: null, percent: null },
    model_source: null,
    tool_mode: null,
    failure_class: null,
    next_action: null,
    resume_url: '/gui',
    can_pause: false,
    can_cancel: false,
    can_retry: false,
    input_revision: null,
    input_snapshot_hash: null,
    process_metrics: null,
    elapsed_seconds: 12,
    heartbeat_at: null,
    last_activity_at: '2026-08-26T09:59:58Z',
    last_output_at: null,
    last_checkpoint_at: null,
    created_at: '2026-08-26T09:59:00Z',
    updated_at: '2026-08-26T09:59:58Z',
    completed_at: null,
    events: null,
    ...overrides,
  }
}

describe('desktop pet operation presentation', () => {
  it.each([
    [[], 'idle'],
    [[operation('queued')], 'thinking'],
    [[operation('running')], 'writing'],
    [[operation('waiting_user')], 'waiting_author'],
    [[operation('paused')], 'waiting_author'],
    [[operation('completed')], 'completed'],
    [[operation('failed')], 'error'],
  ] as const)('maps structured operation status to %s', (operations, expected) => {
    expect(deriveDesktopPetPresentation([...operations], NOW).state).toBe(expected)
  })

  it('uses health status as the deterministic error boundary', () => {
    const presentation = deriveDesktopPetPresentation([
      operation('running', { health_status: 'stalled' }),
    ], NOW)
    expect(presentation.state).toBe('error')
  })

  it('does not infer state from natural-language task text', () => {
    const running = operation('running', { current_message: '等待作者确认下一章' })
    const queued = operation('queued', { current_message: '已经完成整本书' })

    expect(deriveDesktopPetPresentation([running], NOW).state).toBe('writing')
    expect(deriveDesktopPetPresentation([queued], NOW).state).toBe('thinking')
  })

  it('returns to idle after a terminal celebration expires', () => {
    const stale = operation('completed', {
      updated_at: '2026-08-26T09:55:00Z',
      completed_at: '2026-08-26T09:55:00Z',
    })
    expect(deriveDesktopPetPresentation([stale], NOW).state).toBe('idle')
  })

  it('keeps idle business presentation independent from ambient animation', () => {
    expect(deriveDesktopPetPresentation([], NOW).state).toBe('idle')
    expect(deriveDesktopPetPresentation([operation('queued')], NOW).state).toBe('thinking')
    expect(desktopPetToneForState('sleeping')).toBeNull()
  })

  it('shows the unsaved-draft cue while waiting for the author', () => {
    const presentation = deriveDesktopPetPresentation([operation('waiting_user')], NOW)
    expect(presentation.inlineNotice).toBe('稿稿等你点头啦')
  })

  it('surfaces the structured operation error reason without changing state by text', () => {
    const presentation = deriveDesktopPetPresentation([
      operation('failed', { current_message: '模型服务暂时不可用' }),
    ], NOW)

    expect(presentation.state).toBe('error')
    expect(presentation.inlineNotice).toBe('模型服务暂时不可用')
  })

  it('uses the operation action route without exposing write controls', () => {
    const presentation = deriveDesktopPetPresentation([
      operation('waiting_user', {
        attention: {
          kind: 'draft_review',
          title: '草稿待审阅',
          message: '请确认是否保存。',
          action_label: '审阅',
          action_url: '/project/project-1/writer',
          blocking: true,
        },
      }),
    ], NOW)

    expect(presentation.actionRoute).toBe('/project/project-1/writer')
    expect(presentation.actionLabel).toBe('查看任务')
  })
})
