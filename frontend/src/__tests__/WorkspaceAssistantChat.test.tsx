import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Modal, message } from 'antd'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { mockDelete, mockGet, mockPost } = vi.hoisted(() => ({
  mockDelete: vi.fn(),
  mockGet: vi.fn(),
  mockPost: vi.fn(),
}))

vi.mock('../api/client', () => ({
  apiClient: { delete: mockDelete, get: mockGet, post: mockPost },
}))

vi.mock('../shared/operations/queries', () => ({
  useOperations: () => ({ data: [], refetch: vi.fn() }),
}))

import WorkspaceAssistantChat from '../components/WorkspaceAssistantChat'

const encoder = new TextEncoder()

function sse(payload: unknown) {
  return `data: ${typeof payload === 'string' ? payload : JSON.stringify(payload)}\n\n`
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

function createControlledResponse(initialChunks: string[] = []) {
  const chunks = initialChunks.map((chunk) => encoder.encode(chunk))
  let closed = false
  let failure: unknown = null
  let pending: {
    resolve: (value: ReadableStreamReadResult<Uint8Array>) => void
    reject: (reason?: unknown) => void
  } | null = null

  const settle = () => {
    if (!pending) return
    const next = pending
    pending = null
    if (failure) next.reject(failure)
    else if (chunks.length) next.resolve({ done: false, value: chunks.shift()! })
    else if (closed) next.resolve({ done: true, value: undefined })
    else pending = next
  }

  const reader = {
    read: vi.fn(() => {
      if (failure) return Promise.reject(failure)
      if (chunks.length) return Promise.resolve({ done: false, value: chunks.shift()! })
      if (closed) return Promise.resolve({ done: true, value: undefined })
      return new Promise<ReadableStreamReadResult<Uint8Array>>((resolve, reject) => {
        pending = { resolve, reject }
      })
    }),
  }

  return {
    response: { ok: true, status: 200, body: { getReader: () => reader } } as unknown as Response,
    bindSignal(signal?: AbortSignal | null) {
      signal?.addEventListener('abort', () => {
        failure = new DOMException('aborted', 'AbortError')
        settle()
      }, { once: true })
    },
    push(...nextChunks: string[]) {
      chunks.push(...nextChunks.map((chunk) => encoder.encode(chunk)))
      settle()
    },
    close() {
      closed = true
      settle()
    },
  }
}

const conversationEvent = sse({
  type: 'conversation',
  conversation: { id: 'conversation-1', project_id: 'project-1', title: '第一章' },
  user_message: {
    id: 'user-1', conversation_id: 'conversation-1', role: 'user', content: '写第一章', status: 'completed',
  },
  assistant_message: {
    id: 'assistant-1', conversation_id: 'conversation-1', role: 'assistant', content: '正在分析需求...', status: 'running', payload: null,
  },
})

const runEvent = sse({
  type: 'run',
  run: { id: 'run-1', operation_id: 'operation-1', status: 'running', phase: 'writing' },
})

const completedRunDetail = {
  code: 0,
  message: 'ok',
  data: {
    run: { id: 'run-1', operation_id: 'operation-1', status: 'completed', phase: 'completed' },
    assistant_message: {
      id: 'assistant-1',
      conversation_id: 'conversation-1',
      role: 'assistant',
      content: '正文已保存',
      status: 'completed',
      payload: { reply: '正文已保存', tool_logs: [], actions: [], applied_actions: [] },
    },
    steps: [],
  },
}

function renderChat(onApplied = vi.fn()) {
  const view = render(
    <MemoryRouter>
      <WorkspaceAssistantChat
        projectId="project-1"
        defaultModel="openai:test"
        modelOptions={[{ value: 'openai:test', label: 'OpenAI · test' }]}
        onApplied={onApplied}
      />
    </MemoryRouter>,
  )
  return { ...view, onApplied }
}

async function sendChapterRequest() {
  const user = userEvent.setup()
  await user.type(screen.getByPlaceholderText(/告诉AI你想写什么/), '写第一章')
  await user.click(screen.getByRole('button', { name: /发送/ }))
  await screen.findByText('任务已创建：run-1')
  return user
}

describe('WorkspaceAssistantChat cancellation and recovery', () => {
  afterEach(() => {
    Modal.destroyAll()
    message.destroy()
    document.querySelectorAll('.ant-modal-root').forEach((node) => node.remove())
  })

  beforeEach(() => {
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    mockGet.mockResolvedValue({ data: { data: { items: [], total: 0 } } })
    mockDelete.mockResolvedValue({ data: { data: null } })
    mockPost.mockResolvedValue({ data: { data: { status: 'cancelled' } } })
  })

  it('submits only one cancellation and exposes the pending state', async () => {
    const stream = createControlledResponse([conversationEvent + runEvent])
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      stream.bindSignal(init?.signal)
      return Promise.resolve(stream.response)
    }))
    let resolveCancel!: (value: unknown) => void
    mockPost.mockReturnValue(new Promise((resolve) => { resolveCancel = resolve }))
    renderChat()
    await sendChapterRequest()

    const cancelButton = screen.getByRole('button', { name: '取消当前任务' })
    fireEvent.click(cancelButton)
    fireEvent.click(cancelButton)

    expect(mockPost).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: '正在取消任务' })).toBeDisabled()
    await act(async () => {
      resolveCancel({ data: { data: { status: 'cancelled' } } })
    })
    expect(within(screen.getByTestId('project-ai-chat')).getByRole('status')).toHaveTextContent('已停止后续执行')
  })

  it('reveals reasoning deltas character by character before the final answer arrives', async () => {
    const stream = createControlledResponse([
      conversationEvent
      + runEvent
      + sse({ type: 'reasoning_delta', delta: '先核对作品资料', iteration: 1 }),
    ])
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      stream.bindSignal(init?.signal)
      return Promise.resolve(stream.response)
    }))
    renderChat()
    const user = await sendChapterRequest()

    const disclosure = await screen.findByRole('button', { name: /模型思考摘要.*实时生成/ })
    expect(disclosure).toHaveAttribute('aria-expanded', 'true')
    expect(screen.queryByText('资料检查完成。')).not.toBeInTheDocument()
    await waitFor(() => {
      expect(document.querySelector('.assistant-reasoning-text')).toHaveTextContent('先核对作品资料')
    })

    await act(async () => {
      stream.push(
        sse({ type: 'content_delta', delta: '资料检查完成。' })
        + sse({
          type: 'complete',
          data: {
            reply: '资料检查完成。',
            reasoning_content: '先核对作品资料',
            outcome: 'completed_with_reply',
            actions: [],
            applied_actions: [],
            tool_logs: [],
            run: { id: 'run-1', operation_id: 'operation-1', status: 'completed', phase: 'completed' },
          },
        })
        + sse('[DONE]'),
      )
      stream.close()
    })

    expect(await screen.findByText('资料检查完成。')).toBeInTheDocument()
    await waitFor(() => expect(disclosure).toHaveAccessibleName(/模型思考摘要.*已完成/))
    await user.click(disclosure)
    expect(disclosure).toHaveAttribute('aria-expanded', 'false')
  })

  it('recovers the authoritative completed result after a detached stream ends', async () => {
    const stream = createControlledResponse([conversationEvent + runEvent])
    stream.close()
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/workspace-assistant/stream')) {
        stream.bindSignal(init?.signal)
        return Promise.resolve(stream.response)
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => completedRunDetail,
      } as Response)
    })
    vi.stubGlobal('fetch', fetchMock)
    const { onApplied } = renderChat()
    await sendChapterRequest()

    expect(await screen.findByText('正文已保存')).toBeInTheDocument()
    expect(screen.getByText(/已完成 #run-1/)).toBeInTheDocument()
    expect(onApplied).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/projects/project-1/ai/assistant/runs/run-1',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('stops polling immediately on a fatal run lookup error', async () => {
    const stream = createControlledResponse([conversationEvent + runEvent])
    stream.close()
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('/workspace-assistant/stream')) {
        stream.bindSignal(init?.signal)
        return Promise.resolve(stream.response)
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        json: async () => ({ detail: '任务不存在' }),
      } as Response)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderChat()
    await sendChapterRequest()

    expect(await within(screen.getByTestId('project-ai-chat')).findByRole('status')).toHaveTextContent(
      '任务不存在。请在任务中心查看结果。',
    )
    await waitFor(() => expect(screen.getByRole('button', { name: /发送/ })).toBeInTheDocument())
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('reconciles a 409 cancellation response instead of showing a false failure', async () => {
    const stream = createControlledResponse([conversationEvent + runEvent])
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('/workspace-assistant/stream')) {
        stream.bindSignal(init?.signal)
        return Promise.resolve(stream.response)
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => completedRunDetail } as Response)
    })
    vi.stubGlobal('fetch', fetchMock)
    const conflict = Object.assign(new Error('该任务当前不支持此操作'), { response: { status: 409 } })
    mockPost.mockRejectedValue(conflict)
    renderChat()
    const user = await sendChapterRequest()

    await user.click(screen.getByRole('button', { name: '取消当前任务' }))
    expect(await screen.findByText('正文已保存')).toBeInTheDocument()
    expect(within(screen.getByTestId('project-ai-chat')).getByRole('status')).toHaveTextContent('任务已结束')
    expect(mockPost).toHaveBeenCalledTimes(1)
  })

  it('aborts only the browser subscription when unmounted', async () => {
    const stream = createControlledResponse([conversationEvent + runEvent])
    let streamSignal: AbortSignal | undefined
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      streamSignal = init?.signal || undefined
      stream.bindSignal(streamSignal)
      return Promise.resolve(stream.response)
    }))
    const view = renderChat()
    await sendChapterRequest()

    view.unmount()
    expect(streamSignal?.aborted).toBe(true)
    expect(mockPost).not.toHaveBeenCalled()
  })

  it('restores a persisted running task after reload and cancels its operation', async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === '/projects/project-1/ai/assistant/conversations') {
        return Promise.resolve({
          data: { data: { items: [{ id: 'conversation-restored', project_id: 'project-1', title: '后台写章' }], total: 1 } },
        })
      }
      if (url === '/projects/project-1/ai/assistant/conversations/conversation-restored') {
        return Promise.resolve({
          data: {
            data: {
              conversation: { id: 'conversation-restored', project_id: 'project-1', title: '后台写章' },
              messages: [
                {
                  id: 'user-restored', conversation_id: 'conversation-restored', role: 'user',
                  content: '写第一章', status: 'completed',
                },
                {
                  id: 'assistant-restored', conversation_id: 'conversation-restored', role: 'assistant',
                  content: '正在写作', status: 'running',
                  payload: {
                    reply: '正在写作', tool_logs: [], actions: [], applied_actions: [],
                    run: { id: 'run-restored', operation_id: 'operation-restored', status: 'running' },
                  },
                },
              ],
            },
          },
        })
      }
      if (url === '/projects/project-1/ai/assistant/runs/run-restored') {
        return Promise.resolve({
          data: {
            data: {
              run: { id: 'run-restored', operation_id: 'operation-restored', status: 'running' },
              steps: [],
            },
          },
        })
      }
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => (
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
      })
    )))

    const user = userEvent.setup()
    renderChat()

    await user.click(await screen.findByRole('button', { name: '取消当前任务' }))
    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/operations/operation-restored/cancel')
    })
    expect(within(screen.getByTestId('project-ai-chat')).getByRole('status')).toHaveTextContent('已停止后续执行')
  })

  it('converges a restored running task to its persisted completed message', async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === '/projects/project-1/ai/assistant/conversations') {
        return Promise.resolve({
          data: { data: { items: [{ id: 'conversation-1', project_id: 'project-1', title: '第一章' }], total: 1 } },
        })
      }
      if (url === '/projects/project-1/ai/assistant/conversations/conversation-1') {
        return Promise.resolve({
          data: {
            data: {
              conversation: { id: 'conversation-1', project_id: 'project-1', title: '第一章' },
              messages: [{
                id: 'assistant-1', conversation_id: 'conversation-1', role: 'assistant', content: '正在写作', status: 'running',
                payload: {
                  reply: '正在写作', tool_logs: [], actions: [], applied_actions: [],
                  run: { id: 'run-1', operation_id: 'operation-1', status: 'running' },
                },
              }],
            },
          },
        })
      }
      if (url === '/projects/project-1/ai/assistant/runs/run-1') {
        return Promise.resolve({
          data: { data: { run: { id: 'run-1', operation_id: 'operation-1', status: 'running' }, steps: [] } },
        })
      }
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true,
      status: 200,
      json: async () => completedRunDetail,
    } as Response)))

    const { onApplied } = renderChat()

    expect(await screen.findByText('正文已保存')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: /发送/ })).toBeInTheDocument())
    expect(onApplied).toHaveBeenCalledTimes(1)
  })

  it('renders conversation selection and deletion as sibling buttons', async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === '/projects/project-1/ai/assistant/conversations') {
        return Promise.resolve({
          data: { data: { items: [{ id: 'conversation-old', project_id: 'project-1', title: '旧对话' }], total: 1 } },
        })
      }
      return Promise.resolve({
        data: {
          data: {
            conversation: { id: 'conversation-old', project_id: 'project-1', title: '旧对话' },
            messages: [],
          },
        },
      })
    })
    renderChat()

    const selectButton = await screen.findByRole('button', { name: '旧对话' })
    const deleteButton = screen.getByRole('button', { name: '删除对话：旧对话' })
    expect(selectButton.contains(deleteButton)).toBe(false)
    expect(deleteButton.parentElement).toBe(selectButton.parentElement)
  })

  it('does not restore old history after the author starts a new conversation', async () => {
    const oldHistory = deferred<{ data: { data: {
      conversation: { id: string; project_id: string; title: string }
      messages: Array<{
        id: string
        conversation_id: string
        role: 'assistant'
        content: string
        status: string
        payload: null
      }>
    } } }>()
    mockGet.mockImplementation((url: string) => {
      if (url === '/projects/project-1/ai/assistant/conversations') {
        return Promise.resolve({
          data: { data: { items: [{ id: 'conversation-old', project_id: 'project-1', title: '旧对话' }], total: 1 } },
        })
      }
      if (url === '/projects/project-1/ai/assistant/conversations/conversation-old') return oldHistory.promise
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })

    const user = userEvent.setup()
    renderChat()
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith(
      '/projects/project-1/ai/assistant/conversations/conversation-old',
    ))

    await user.click(screen.getByRole('button', { name: '新对话' }))
    await act(async () => {
      oldHistory.resolve({ data: { data: {
        conversation: { id: 'conversation-old', project_id: 'project-1', title: '旧对话' },
        messages: [{
          id: 'old-assistant', conversation_id: 'conversation-old', role: 'assistant',
          content: '迟到的项目对话', status: 'completed', payload: null,
        }],
      } } })
      await oldHistory.promise
    })

    expect(screen.queryByText('迟到的项目对话')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(/告诉AI你想写什么/)).toHaveValue('')
  })

  it('always promotes a generated chapter draft through the chapter create endpoint', async () => {
    const stream = createControlledResponse([conversationEvent + runEvent])
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      stream.bindSignal(init?.signal)
      return Promise.resolve(stream.response)
    }))
    const user = userEvent.setup()
    renderChat()
    await sendChapterRequest()

    const draftAction = {
      tool: 'chapter_writer',
      status: 'ok',
      detail: '第二章草稿已生成，尚未保存',
      data: {
        draft_id: 'draft-chapter-2',
        project_id: 'project-1',
        title: '第二章 夜雨',
        outline_node_id: 'outline-2',
        content: '夜雨落在山门外。',
        context_manifest_id: 'manifest-2',
        draft_status: 'pending',
        target_chapter_id: 'chapter-1',
      },
    }
    await act(async () => {
      stream.push(
        sse({
          type: 'complete',
          data: {
            reply: '第二章草稿已生成，尚未保存。',
            outcome: 'completed_with_reply',
            actions: [draftAction],
            applied_actions: [draftAction],
            tool_logs: [draftAction],
            run: { id: 'run-1', operation_id: 'operation-1', status: 'completed', phase: 'completed' },
          },
        }) + sse('[DONE]'),
      )
      stream.close()
    })

    mockPost.mockResolvedValueOnce({
      data: {
        data: {
          id: 'chapter-2',
          cataloging_job: { started: true },
        },
      },
    })
    await user.click(await screen.findByRole('button', { name: '保存并建档' }))

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        '/projects/project-1/chapters',
        expect.objectContaining({
          draft_id: 'draft-chapter-2',
          outline_node_id: 'outline-2',
          content: '夜雨落在山门外。',
          cataloging_mode: 'save_and_catalog',
        }),
      )
    })
    const lastPostPayload = mockPost.mock.calls[mockPost.mock.calls.length - 1]?.[1]
    expect(lastPostPayload).not.toHaveProperty('target_chapter_id')
  })

  it('shows a rejected chat draft save instead of failing silently', async () => {
    const stream = createControlledResponse([conversationEvent + runEvent])
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      stream.bindSignal(init?.signal)
      return Promise.resolve(stream.response)
    }))
    const user = userEvent.setup()
    renderChat()
    await sendChapterRequest()

    const draftAction = {
      tool: 'chapter_writer',
      status: 'ok',
      detail: '第二章草稿已生成，尚未保存',
      data: {
        draft_id: 'draft-save-error',
        project_id: 'project-1',
        title: '第二章 夜雨',
        outline_node_id: 'outline-2',
        content: '夜雨落在山门外。',
        draft_status: 'pending',
      },
    }
    await act(async () => {
      stream.push(
        sse({
          type: 'complete',
          data: {
            reply: '第二章草稿已生成，尚未保存。',
            outcome: 'completed_with_reply',
            actions: [draftAction],
            applied_actions: [draftAction],
            tool_logs: [draftAction],
            run: { id: 'run-1', operation_id: 'operation-1', status: 'completed', phase: 'completed' },
          },
        }) + sse('[DONE]'),
      )
      stream.close()
    })

    const detail = '该大纲已在草稿生成期间关联正式章节；迟到草稿已释放'
    mockPost.mockRejectedValueOnce(Object.assign(new Error('保存失败'), {
      response: { data: { detail } },
    }))
    await user.click(await screen.findByRole('button', { name: '保存并建档' }))

    expect(await screen.findByText(detail)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /保存并建档/ })).toBeEnabled()
    })
  })

  it('connects OpenCode to the isolated one-turn MCP without a grant step', async () => {
    render(
      <MemoryRouter>
        <WorkspaceAssistantChat
          projectId="project-1"
          defaultModel="opencode_cli:opencode/big-pickle"
          modelOptions={[{
            value: 'opencode_cli:opencode/big-pickle',
            label: 'opencode CLI · opencode/big-pickle',
          }]}
        />
      </MemoryRouter>,
    )

    expect(await screen.findByText('本机 CLI 已连接本轮临时 Siming MCP')).toBeInTheDocument()
    expect(screen.getByText(/只开放当前作品范围的工具/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '授权下一条代理操作' })).not.toBeInTheDocument()
  })

  it('shows an editable outline draft card and confirms before any formal write', async () => {
    const stream = createControlledResponse([conversationEvent + runEvent])
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      stream.bindSignal(init?.signal)
      return Promise.resolve(stream.response)
    }))
    const user = userEvent.setup()
    renderChat()
    await sendChapterRequest()
    const action = {
      tool: 'outline_writer',
      status: 'ok',
      detail: '大纲草稿已生成，等待作者确认',
      data: {
        draft_id: 'outline-draft-1',
        project_id: 'project-1',
        insert_after_id: 'outline-1',
        draft_status: 'pending',
        nodes: [{ node_type: 'chapter', title: '第二章 夜雨', summary: '夜雨袭城。', status: 'pending' }],
      },
    }
    await act(async () => {
      stream.push(sse({
        type: 'complete',
        data: {
          reply: '大纲草稿已生成。',
          applied_actions: [action],
          actions: [action],
          tool_logs: [action],
          run: { id: 'run-1', operation_id: 'operation-1', status: 'completed', phase: 'completed' },
        },
      }) + sse('[DONE]'))
      stream.close()
    })

    expect(await screen.findByText('大纲草稿已生成，等待作者确认')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /查看并编辑/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认并写章' })).toBeInTheDocument()

    mockPost.mockResolvedValueOnce({
      data: { data: { draft_status: 'confirmed', saved_outline_node_ids: ['outline-2'] } },
    })
    await user.click(screen.getByRole('button', { name: /确认大纲/ }))
    expect(mockPost).toHaveBeenCalledWith(
      '/projects/project-1/outline-drafts/outline-draft-1/confirm',
      { write_after_confirm: false },
    )
  })

  it('starts confirm-and-write as a separate author Agent request', async () => {
    const first = createControlledResponse([conversationEvent + runEvent])
    const second = createControlledResponse([
      sse({
        type: 'conversation',
        conversation: { id: 'conversation-2', project_id: 'project-1', title: '写第二章' },
        user_message: { id: 'user-2', role: 'user', content: '请根据真实大纲 ID 写章', status: 'completed' },
        assistant_message: { id: 'assistant-2', role: 'assistant', content: '正在分析需求...', status: 'running' },
      }),
    ])
    const fetchMock = vi.fn()
      .mockImplementationOnce((_input: RequestInfo | URL, init?: RequestInit) => {
        first.bindSignal(init?.signal)
        return Promise.resolve(first.response)
      })
      .mockImplementationOnce((_input: RequestInfo | URL, init?: RequestInit) => {
        second.bindSignal(init?.signal)
        return Promise.resolve(second.response)
      })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderChat()
    await sendChapterRequest()
    const action = {
      tool: 'outline_writer',
      status: 'ok',
      data: {
        draft_id: 'outline-draft-write',
        project_id: 'project-1',
        draft_status: 'pending',
        nodes: [{ node_type: 'chapter', title: '第二章 夜雨', summary: '夜雨袭城。', status: 'pending' }],
      },
    }
    await act(async () => {
      first.push(sse({
        type: 'complete',
        data: {
          reply: '请确认。',
          applied_actions: [action],
          actions: [action],
          tool_logs: [action],
          run: { id: 'run-1', operation_id: 'operation-1', status: 'completed', phase: 'completed' },
        },
      }) + sse('[DONE]'))
      first.close()
    })
    mockPost.mockResolvedValueOnce({
      data: {
        data: {
          draft_status: 'confirmed',
          saved_outline_node_ids: ['outline-real-2'],
          next_author_request: {
            requires_new_agent_turn: true,
            outline_node_id: 'outline-real-2',
            message: '请根据刚确认的章级大纲（ID：outline-real-2）写这一章。',
          },
        },
      },
    })

    await user.click(await screen.findByRole('button', { name: '确认并写章' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    const secondBody = JSON.parse(String(fetchMock.mock.calls[1][1]?.body))
    expect(secondBody.message).toContain('outline-real-2')
    expect(mockPost).toHaveBeenCalledWith(
      '/projects/project-1/outline-drafts/outline-draft-write/confirm',
      { write_after_confirm: true },
    )
    second.close()
  })

  it('requires a separate one-turn read snapshot confirmation for a pasted path', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <WorkspaceAssistantChat
          projectId="project-1"
          defaultModel="opencode_cli:opencode/big-pickle"
          modelOptions={[{
            value: 'opencode_cli:opencode/big-pickle',
            label: 'opencode CLI · opencode/big-pickle',
          }]}
        />
      </MemoryRouter>,
    )

    await user.type(
      screen.getByPlaceholderText(/告诉AI你想写什么/),
      '请读取 "C:\\Novel Notes\\世界观.md"',
    )
    await user.click(screen.getByRole('button', { name: /发送/ }))

    expect(await screen.findByRole('dialog', { name: '仅允许 OpenCode 读取这些路径一次？' })).toBeInTheDocument()
    expect(screen.getByText('C:\\Novel Notes\\世界观.md')).toBeInTheDocument()
    expect(screen.getByText(/只能读取隔离副本/)).toBeInTheDocument()
    expect(screen.getByText(/本轮结束后快照自动删除/)).toBeInTheDocument()
  })
})
