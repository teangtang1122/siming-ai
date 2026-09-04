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
  it('shows the durable cataloging phase and saved artifact counts', () => {
    render(
      <CatalogingJobControlCard
        job={{
          ...job('running'),
          current_chapter_id: 'chapter-13',
          current_stage: 'candidates',
          current_message: '模型进程仍在计算',
          last_activity_at: '2026-08-31T12:34:56Z',
        }}
        currentRun={{
          id: 'run-13',
          chapter_id: 'chapter-13',
          chapter_title: '潮痕之上',
          chapter_order: 12,
          status: 'facts_saved',
        }}
        factCount={19}
        candidateCount={0}
        progress={60}
        streaming
        {...handlers}
      />,
    )

    expect(screen.getByText('当前章节：潮痕之上')).toBeInTheDocument()
    expect(screen.getByText('事实已保存，正在生成候选')).toBeInTheDocument()
    expect(screen.getByText('候选生成')).toBeInTheDocument()
    expect(screen.getByText('模型进程仍在计算')).toBeInTheDocument()
    expect(screen.getByText(/最近活动：/)).toBeInTheDocument()
    expect(screen.getByText('已保存 19 条事实 · 0 条候选')).toBeInTheDocument()
  })

  it('shows automatic candidate application as progress, not a user confirmation barrier', () => {
    render(
      <CatalogingJobControlCard
        job={{
          ...job('running'),
          execution_mode: 'auto',
          execution_backend: 'local_cli_agent',
          current_chapter_id: 'chapter-13',
        }}
        currentRun={{
          id: 'run-13',
          chapter_id: 'chapter-13',
          chapter_title: '潮痕之上',
          chapter_order: 12,
          status: 'awaiting_confirmation',
        }}
        factCount={19}
        candidateCount={8}
        progress={60}
        streaming
        {...handlers}
      />,
    )

    expect(screen.getByText('候选应用中')).toBeInTheDocument()
    expect(screen.queryByText('候选待确认')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /写入并继续/ })).not.toBeInTheDocument()
  })

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

  it('presents source-coverage differences as review work instead of a failed rerun', () => {
    render(
      <CatalogingJobControlCard
        job={{
          ...job('waiting_confirmation'),
          review_warning: '候选已保留，需要核对模型抽取的原文线索：原文角色未进入章节覆盖清单：爷爷',
        }}
        progress={60}
        streaming={false}
        {...handlers}
      />,
    )

    expect(screen.getByText('候选已生成，有原文线索需要你核对')).toBeInTheDocument()
    expect(screen.getAllByText(/候选已保留，需要核对模型抽取的原文线索/)).toHaveLength(1)
    expect(screen.getByRole('button', { name: /写入并继续/ })).toBeEnabled()
  })
})
