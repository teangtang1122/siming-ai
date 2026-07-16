import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import CatalogingJobControlCard from '../pages/CatalogingJobControlCard'
import type { CatalogingJob } from '../pages/catalogingTypes'

const handlers = {
  onApplyPending: vi.fn(),
  onRetryCurrent: vi.fn(),
  onRerunResolutionCurrent: vi.fn(),
  onRecoverCurrent: vi.fn(),
  onSkipCurrent: vi.fn(),
  onPauseCurrentJob: vi.fn(),
  onCancelCurrentJob: vi.fn(),
  onResumeCurrentJob: vi.fn(),
  onStreamJob: vi.fn(),
}

function job(status: string): CatalogingJob {
  return {
    id: 'job-1',
    project_id: 'project-1',
    status,
    execution_mode: 'manual',
    total_chapters: 20,
    completed_chapters: 12,
    failed_chapters: 0,
  }
}

describe('CatalogingJobControlCard', () => {
  it('keeps the current chapter visibly waiting for confirmation', () => {
    render(
      <CatalogingJobControlCard
        job={job('waiting_confirmation')}
        progress={60}
        streaming={false}
        {...handlers}
      />,
    )

    expect(screen.getByText('当前章节已生成档案候选，等待你确认')).toBeInTheDocument()
    expect(screen.getByText('已完成：12 章已完成')).toBeInTheDocument()
    expect(screen.getByText('未完成：确认或调整当前章节候选项')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /写入并继续/ }))
    expect(handlers.onApplyPending).toHaveBeenCalled()
  })

  it('describes a paused failure as partial success instead of generic completion', () => {
    render(
      <CatalogingJobControlCard
        job={{ ...job('paused_on_failure'), error: '第13章候选格式不完整' }}
        progress={60}
        streaming={false}
        {...handlers}
      />,
    )

    expect(screen.getByText('当前章节遇到问题，任务已停在最近检查点')).toBeInTheDocument()
    expect(screen.getAllByText('第13章候选格式不完整')).toHaveLength(1)
    expect(screen.getByText('未完成：8 章尚未完成')).toBeInTheDocument()
  })
})
