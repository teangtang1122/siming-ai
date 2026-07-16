import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import {
  AdaptiveHelp,
  PersistentActionBar,
  PersistentOutcome,
  SaveStatusIndicator,
} from '../components/interaction'

describe('shared interaction components', () => {
  it('keeps partial results and unfinished work visible', () => {
    render(
      <PersistentOutcome
        outcome="partial_success"
        result={{
          summary: '正文已经保存，但档案更新失败。',
          completed: ['章节正文'],
          incomplete: ['角色状态归档'],
        }}
      />,
    )

    expect(screen.getByText('部分完成')).toBeInTheDocument()
    expect(screen.getByText(/已完成：章节正文/)).toBeInTheDocument()
    expect(screen.getByText(/未完成：角色状态归档/)).toBeInTheDocument()
  })

  it('announces saving failures next to the editing surface', () => {
    render(<SaveStatusIndicator status="error" error="网络断开，请重试。" />)

    expect(screen.getByRole('status')).toHaveTextContent('保存失败')
    expect(screen.getByText('网络断开，请重试。')).toBeInTheDocument()
  })

  it('provides a named persistent action region', () => {
    render(<PersistentActionBar label="章节保存操作"><button>保存章节</button></PersistentActionBar>)

    expect(screen.getByRole('region', { name: '章节保存操作' })).toBeInTheDocument()
  })

  it('lets authors dismiss contextual help without a forced tutorial', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    render(<AdaptiveHelp preferenceKey="test-help" title="这里可以继续" description="修改后记得保存。" />)

    fireEvent.click(screen.getByRole('button', { name: '关闭提示' }))
    expect(setItem).toHaveBeenCalledWith('siming_help_dismissed_test-help', 'true')
  })
})
