import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

const { mockGet, mockPost, mockDelete, mockNavigate, modelState } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockDelete: vi.fn(),
  mockNavigate: vi.fn(),
  modelState: { defaultModel: 'openai:test' },
}))

vi.mock('../api/client', () => ({
  apiClient: { get: mockGet, post: mockPost, delete: mockDelete },
}))

vi.mock('../hooks/useModelOptions', () => ({
  useModelOptions: () => ({
    defaultModel: modelState.defaultModel,
    loading: false,
    modelOptions: [
      { value: 'openai:test', label: 'OpenAI · test', provider: 'openai', model: 'test' },
      { value: 'anthropic:sonnet', label: 'Anthropic Claude · sonnet', provider: 'anthropic', model: 'sonnet' },
    ],
  }),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

import GuiAssistantChat from '../components/GuiAssistantChat'

describe('GuiAssistantChat new-book handoff', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    modelState.defaultModel = 'openai:test'
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
    mockGet.mockImplementation((url: string) => {
      if (url === '/projects') return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      if (url === '/ai/system-assistant/conversations') return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    mockPost.mockImplementation((url: string) => {
      if (url === '/novel-creation/start') return Promise.resolve({ data: { data: { session_id: 'session-1' } } })
      if (url === '/novel-creation/sessions/session-1/interview/next') {
        return Promise.resolve({
          data: {
            data: {
              session_id: 'session-1',
              state: 'ready',
              history: [],
              runtime: {
                effective_model: 'openai:test',
                provider: 'openai',
                model_source: 'global_default',
                tool_mode: 'api_text_json',
                timeout_seconds: 30,
                quota_status: 'unknown',
              },
            },
          },
        })
      }
      if (url === '/novel-creation/sessions/session-1/runs') {
        return Promise.resolve({ data: { data: { run: { id: 'run-1', status: 'running' } } } })
      }
      if (url === '/ai/system-assistant/conversations') {
        return Promise.resolve({ data: { data: { conversation: { id: 'conversation-1', title: '新书' } } } })
      }
      if (url === '/ai/system-assistant/conversations/conversation-1/turns') {
        return Promise.resolve({ data: { data: { conversation: { id: 'conversation-1', title: '新书' } } } })
      }
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })
  })

  it('offers the free setup flow when no model is configured', async () => {
    modelState.defaultModel = ''
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    expect(await screen.findByText('还差一步：先连接一个模型')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '免费设置' }))
    expect(mockNavigate).toHaveBeenCalledWith('/getting-started')
  })

  it('hands a completed interview to a compact concept run instead of drafting full blueprints', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '我要创建新的小说')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/novel-creation?session=session-1&run=run-1')
    })
    expect(mockPost).toHaveBeenCalledWith('/novel-creation/sessions/session-1/interview/next', expect.objectContaining({
      qa_history: [],
    }))
    expect(mockPost).toHaveBeenCalledWith('/novel-creation/sessions/session-1/runs', expect.objectContaining({
      stage: 'concepts',
      operation: 'generate_concepts',
    }))
    expect(mockPost).not.toHaveBeenCalledWith('/novel-creation/draft', expect.anything())
  })

  it('makes the current conversation model visible', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    await user.click(await screen.findByRole('button', { name: '查看当前模型与运行状态' }))
    expect(await screen.findByRole('combobox', { name: '选择本次对话模型' })).toBeInTheDocument()
    expect(screen.getByText('OpenAI · test')).toBeInTheDocument()
  })

  it('shows actual runtime diagnostics after an interview response', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '\u6211\u60f3\u521b\u5efa\u4e00\u672c\u65b0\u7684\u5c0f\u8bf4')
    await user.click(screen.getByRole('button', { name: /\u53d1\u9001/ }))

    await user.click(screen.getByRole('button', { name: '\u67e5\u770b\u5f53\u524d\u6a21\u578b\u4e0e\u8fd0\u884c\u72b6\u6001' }))
    await waitFor(() => {
      const runtime = screen.getByLabelText('\u5f53\u524d\u6a21\u578b\u8fd0\u884c\u72b6\u6001')
      expect(runtime).toHaveTextContent('\u63d0\u4f9b\u5546')
      expect(runtime).toHaveTextContent('openai')
      expect(runtime).toHaveTextContent('openai:test')
      expect(runtime).toHaveTextContent('\u5168\u5c40\u9ed8\u8ba4')
      expect(runtime).toHaveTextContent('\u52a8\u6001\u91c7\u8bbf JSON')
      expect(runtime).toHaveTextContent('30 \u79d2')
    })
  })

  it('marks a failed system chat as an error instead of a completion', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '你好')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    const errorMessage = await screen.findByRole('alert')
    expect(errorMessage).toHaveAttribute('data-message-status', 'error')
    expect(errorMessage).toHaveTextContent('unexpected POST /novel-creation/system-chat')
  })

  it('marks an empty system chat reply as an error instead of a completion', async () => {
    mockPost.mockImplementation((url: string) => {
      if (url === '/novel-creation/system-chat') return Promise.resolve({ data: { data: { reply: '' } } })
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '你好')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    const errorMessage = await screen.findByRole('alert')
    expect(errorMessage).toHaveAttribute('data-message-status', 'error')
    expect(errorMessage).toHaveTextContent('当前模型没有返回文字回复')
  })

  it('marks a failed interview skip as an error instead of a completion', async () => {
    mockPost.mockImplementation((url: string) => {
      if (url === '/novel-creation/start') return Promise.resolve({ data: { data: { session_id: 'session-1' } } })
      if (url === '/novel-creation/sessions/session-1/interview/next') {
        const priorCalls = mockPost.mock.calls.filter(([calledUrl]) => calledUrl === url).length
        if (priorCalls === 1) {
          return Promise.resolve({
            data: {
              data: {
                session_id: 'session-1',
                state: 'question',
                question: { question: '主角最想得到什么？', type: 'choice', options: ['自由'] },
                history: [],
              },
            },
          })
        }
        return Promise.reject(new Error('模型额度已耗尽'))
      }
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })

    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '我想创建一本新的小说')
    await user.click(screen.getByRole('button', { name: /发送/ }))
    await user.click(await screen.findByRole('button', { name: '跳过并生成创意方向' }))

    const errorMessage = await screen.findByRole('alert')
    expect(errorMessage).toHaveAttribute('data-message-status', 'error')
    expect(errorMessage).toHaveTextContent('模型额度已耗尽')
    expect(errorMessage).toHaveTextContent('执行失败')
    expect(screen.queryByText('主角最想得到什么？')).not.toBeInTheDocument()
  })

  it('renders quota exhaustion as an error with the actual CLI diagnostics', async () => {
    let interviewCalls = 0
    mockPost.mockImplementation((url: string) => {
      if (url === '/novel-creation/start') return Promise.resolve({ data: { data: { session_id: 'session-1' } } })
      if (url === '/novel-creation/sessions/session-1/interview/next') {
        interviewCalls += 1
        if (interviewCalls === 1) {
          return Promise.resolve({
            data: {
              data: {
                session_id: 'session-1',
                state: 'question',
                question: { question: '\u5f00\u5c40\u7684\u4ee3\u4ef7\u662f\u4ec0\u4e48\uff1f', type: 'text' },
                history: [],
              },
            },
          })
        }
        return Promise.reject({
          response: {
            data: {
              detail: {
                message: 'Free usage exceeded, retrying in 9h',
                failure_class: 'quota_or_rate_limit',
                next_action: '\u5207\u6362\u6709\u989d\u5ea6\u7684\u6a21\u578b\u540e\u91cd\u8bd5\u3002',
                runtime: {
                  effective_model: 'opencode_cli:free-model',
                  provider: 'opencode_cli',
                  model_source: 'conversation_override',
                  tool_mode: 'local_cli_text_json',
                  timeout_seconds: 45,
                  quota_status: 'exhausted_or_limited',
                },
              },
            },
          },
        })
      }
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })

    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '\u6211\u60f3\u521b\u5efa\u4e00\u672c\u65b0\u7684\u5c0f\u8bf4')
    await user.click(screen.getByRole('button', { name: /\u53d1\u9001/ }))
    await user.click(await screen.findByRole('button', { name: '\u8df3\u8fc7\u5e76\u751f\u6210\u521b\u610f\u65b9\u5411' }))

    await user.click(screen.getByRole('button', { name: '\u67e5\u770b\u5f53\u524d\u6a21\u578b\u4e0e\u8fd0\u884c\u72b6\u6001' }))
    await waitFor(() => {
      const errorMessage = screen.getByRole('alert')
      expect(errorMessage).toHaveAttribute('data-message-status', 'error')
      expect(errorMessage).toHaveTextContent('Free usage exceeded')
      expect(screen.getByLabelText('\u5f53\u524d\u6a21\u578b\u8fd0\u884c\u72b6\u6001')).toHaveTextContent('\u5df2\u8017\u5c3d\u6216\u9650\u6d41')
      expect(screen.getByLabelText('\u5f53\u524d\u6a21\u578b\u8fd0\u884c\u72b6\u6001')).toHaveTextContent('opencode_cli:free-model')
      expect(screen.getByLabelText('\u5f53\u524d\u6a21\u578b\u8fd0\u884c\u72b6\u6001')).toHaveTextContent('45 \u79d2')
    })
  })
})
