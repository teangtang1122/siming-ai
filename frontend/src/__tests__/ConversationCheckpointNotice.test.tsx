import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ConversationCheckpointNotice } from '../components/assistant/ConversationCheckpointNotice'

const handlers = () => ({
  onOpen: vi.fn(),
  onClose: vi.fn(),
  onRebuild: vi.fn(),
  onCancel: vi.fn(),
  onNewConversation: vi.fn(),
})

describe('ConversationCheckpointNotice', () => {
  it('can hide cancellation when the caller has no independently safe cancel API', () => {
    const actions = handlers()
    render(
      <ConversationCheckpointNotice
        state={{ status: 'compressing', latest_checkpoint_id: 'checkpoint-1' }}
        modalOpen
        canCancel={false}
        {...actions}
      />,
    )

    expect(screen.getByText('正在整理较早上下文')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '取消整理' })).not.toBeInTheDocument()
    expect(actions.onCancel).not.toHaveBeenCalled()
  })

  it('keeps failed checkpoints recoverable through a new author message', () => {
    const actions = handlers()
    render(
      <ConversationCheckpointNotice
        state={{
          status: 'failed',
          latest_checkpoint_id: 'checkpoint-failed',
          error_detail: '结构校验失败',
          retryable: true,
        }}
        modalOpen={false}
        canCancel={false}
        {...actions}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /发送新消息重试/ }))
    expect(actions.onRebuild).toHaveBeenCalledOnce()
  })
})
