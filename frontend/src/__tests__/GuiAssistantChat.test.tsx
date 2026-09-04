import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { message } from 'antd'

const { mockGet, mockPost, mockPostForm, mockPatch, mockDelete, mockNavigate, mockAgentTurn, modelState } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockPostForm: vi.fn(),
  mockPatch: vi.fn(),
  mockDelete: vi.fn(),
  mockNavigate: vi.fn(),
  mockAgentTurn: vi.fn(),
  modelState: { defaultModel: 'openai:test' },
}))

vi.mock('../api/client', () => ({
  apiClient: { get: mockGet, post: mockPost, postForm: mockPostForm, patch: mockPatch, delete: mockDelete },
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

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

describe('GuiAssistantChat new-book handoff', () => {
  afterEach(() => {
    message.destroy()
    message.config({ duration: 3 })
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  beforeEach(() => {
    message.config({ duration: 0 })
    vi.clearAllMocks()
    modelState.defaultModel = 'openai:test'
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
    mockAgentTurn.mockImplementation((body: Record<string, unknown>) => ({
      reply: '已按你的要求读取立项数据并启动局部调整。',
      run: {
        id: 'run-characters',
        session_id: 'session-1',
        stage: String(body.message || '').includes('角色') ? 'characters' : 'world_style',
        status: 'running',
        operation_id: 'operation-characters',
        current_message: '正在调用立项工具',
      },
      tool_results: [],
      message_status: 'running',
      conversation_id: 'conversation-1',
      assistant_message_id: 'assistant-1',
      turn_persisted: true,
    }))
    vi.stubGlobal('fetch', vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body || '{}')) as Record<string, unknown>
      const result = await mockAgentTurn(body)
      const clientTurnId = String(body.client_turn_id || 'client-turn-test')
      const events = [
        { client_turn_id: clientTurnId, sequence: 1, type: 'turn_started', message: '已接收请求，正在准备立项上下文…', data: {} },
        { client_turn_id: clientTurnId, sequence: 2, type: 'tool_categories_changed', message: '已准备立项资料能力', data: { enabled_categories: ['creation_data'] } },
        { client_turn_id: clientTurnId, sequence: 3, type: 'reply_delta', message: '', data: { delta: result.reply } },
        { client_turn_id: clientTurnId, sequence: 4, type: 'complete', message: '本轮立项处理完成', data: result },
      ]
      const chunks = events.map((event) => new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`))
      let index = 0
      return {
        ok: true,
        status: 200,
        body: {
          getReader: () => ({
            read: async () => index < chunks.length
              ? { done: false, value: chunks[index++] }
              : { done: true, value: undefined },
            releaseLock: vi.fn(),
          }),
        },
        json: async () => ({}),
      }
    }))
    mockGet.mockImplementation((url: string) => {
      if (url === '/projects') return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      if (url === '/ai/assistant/conversations') return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [] } } })
      if (url === '/novel-creation/sessions/session-1/artifacts') return Promise.resolve({ data: { data: {
        revision: 7,
        artifacts: [
          {
            artifact: 'concepts',
            label: '创意方案',
            status: 'generated',
            source: 'model',
            revision: 7,
            locked_paths: [],
            can_undo: true,
            checkpoint_count: 1,
            version_count: 2,
            latest_version_id: 'version-new',
            flow: { can_confirm: true, soft_dependencies: [] },
          },
          {
            artifact: 'characters',
            label: '角色与关系',
            status: 'stale',
            source: 'assistant',
            revision: 7,
            stale_reason: '上游创意方案已修改',
            locked_paths: ['/characters/0'],
            flow: {
              can_view: true,
              can_confirm: false,
              soft_dependencies: [{ stage: 'world_style', label: '文风与世界观', reason: 'not_confirmed', message: '仍可生成' }],
            },
          },
        ],
      } } })
      if (url === '/novel-creation/sessions/session-1/artifacts/concepts') {
        return Promise.resolve({ data: { data: {
          artifact: 'concepts', label: '创意方案', status: 'generated', source: 'model', revision: 7,
          data: { options: [{ title: '灰港遗忘症', logline: '用遗忘交换线索' }] },
        } } })
      }
      if (url === '/novel-creation/sessions/session-1/artifacts/concepts/versions') {
        return Promise.resolve({ data: { data: { versions: [
          {
            id: 'version-new', session_id: 'session-1', artifact: 'concepts', revision: 7,
            status: 'generated', source: 'assistant', change_type: 'patch', parent_version_id: 'version-old',
            created_at: '2026-08-02T12:00:00Z',
          },
          {
            id: 'version-old', session_id: 'session-1', artifact: 'concepts', revision: 5,
            status: 'generated', source: 'model', change_type: 'generate', parent_version_id: null,
            created_at: '2026-08-02T11:00:00Z',
          },
        ] } } })
      }
      if (url === '/novel-creation/artifact-versions/version-new') {
        return Promise.resolve({ data: { data: {
          version: { id: 'version-new', revision: 7 },
          against: { id: 'version-old', revision: 5 },
          changes: [{ path: '/concepts/0/title', action: 'replace', before: '旧方案', after: '新方案' }],
          change_count: 1,
          truncated: false,
        } } })
      }
      if (url === '/novel-creation/artifact-versions/version-old') {
        return Promise.resolve({ data: { data: {
          version: { id: 'version-old', revision: 5 },
          against: null,
          changes: [{ path: '/concepts', action: 'add', after: [{ title: '旧方案' }] }],
          change_count: 1,
          truncated: false,
        } } })
      }
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    mockPost.mockImplementation((url: string) => {
      if (url === '/novel-creation/start') return Promise.resolve({ data: { data: {
        session_id: 'session-1',
        session: { id: 'session-1', user_brief: '', status: 'drafting', revision: 1 },
      } } })
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
        return Promise.resolve({ data: { data: { run: {
          id: 'run-1',
          session_id: 'session-1',
          stage: 'concepts',
          status: 'running',
          operation_id: 'operation-1',
          current_message: '正在生成创意方向',
        } } } })
      }
      if (url === '/novel-creation/sessions/session-1/stages/concepts/confirm') {
        return Promise.resolve({ data: { data: { id: 'session-1', revision: 8 } } })
      }
      if (url === '/novel-creation/sessions/session-1/artifacts/concepts/undo') {
        return Promise.resolve({ data: { data: { artifact: { artifact: 'concepts', revision: 8 } } } })
      }
      if (url === '/novel-creation/artifact-versions/version-old/restore') {
        return Promise.resolve({ data: { data: { artifact: { artifact: 'concepts', revision: 8 } } } })
      }
      if (url === '/ai/assistant/conversations') {
        return Promise.resolve({ data: { data: { conversation: { id: 'conversation-1', title: '新书' } } } })
      }
      if (url === '/ai/assistant/conversations/conversation-1/turns') {
        return Promise.resolve({ data: { data: { conversation: { id: 'conversation-1', title: '新书' } } } })
      }
      if (url === '/ai/assistant/conversations/conversation-1/turns/start') {
        return Promise.resolve({ data: { data: {
          conversation: { id: 'conversation-1', title: '新书', scope_type: 'creation', creation_session_id: 'session-1' },
          messages: [{ id: 'user-1' }, { id: 'assistant-1' }],
        } } })
      }
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })
    mockPatch.mockResolvedValue({ data: { data: {} } })
  })

  it('starts new-book input without navigating back to the same assistant or sending it', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    await user.click(await screen.findByRole('button', { name: /开始新书立项/ }))
    const input = screen.getByRole('textbox', { name: '给司命的消息' })
    expect(input).toHaveValue('我想创作一本新小说，请先和我确认题材、故事构想与篇幅。')
    await waitFor(() => expect(input).toHaveFocus())
    expect(mockNavigate).not.toHaveBeenCalled()
    expect(mockPost).not.toHaveBeenCalled()
    expect(mockAgentTurn).not.toHaveBeenCalled()
  })

  it('keeps an author-entered brief when starting a new book from the welcome screen', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    const button = await screen.findByRole('button', { name: /开始新书立项/ })
    const input = screen.getByRole('textbox', { name: '给司命的消息' })
    await user.type(input, '海边修复师调查潮汐录音')
    await user.click(button)
    expect(input).toHaveValue('海边修复师调查潮汐录音')
    expect(mockAgentTurn).not.toHaveBeenCalled()
  })

  it('persists project assistant turns in the canonical project-scoped conversation', async () => {
    localStorage.setItem('siming.gui.assistant.projectId', 'project-1')
    mockGet.mockImplementation((url: string) => {
      if (url === '/projects') {
        return Promise.resolve({ data: { data: { items: [{ id: 'project-1', title: '测试作品' }], total: 1 } } })
      }
      if (url === '/ai/assistant/conversations') {
        return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      }
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    mockPost.mockImplementation((url: string, body: any) => {
      if (url === '/ai/assistant/conversations') {
        expect(body).toMatchObject({ scope_type: 'project', scope_id: 'project-1' })
        return Promise.resolve({ data: { data: { conversation: { id: 'project-conversation-1', title: '讨论' } } } })
      }
      if (url === '/ai/assistant/conversations/project-conversation-1/turns/start') {
        expect(body).toMatchObject({ scope_type: 'project', scope_id: 'project-1', user_content: '调整主角动机' })
        return Promise.resolve({ data: { data: {
          conversation: { id: 'project-conversation-1', title: '讨论', scope_type: 'project', scope_id: 'project-1' },
          messages: [{ id: 'user-1' }, { id: 'assistant-1' }],
        } } })
      }
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })
    const stream = [
      'data: ' + JSON.stringify({ type: 'reasoning_delta', delta: '先读取角色档案，再核对当前目标。', iteration: 1 }),
      'data: ' + JSON.stringify({
        type: 'complete',
        data: {
          reply: '已调整主角动机',
          reasoning_content: '先读取角色档案，再核对当前目标。',
          run: { id: 'run-1', operation_id: 'operation-1' },
        },
      }),
      'data: [DONE]',
      '',
    ].join('\n\n')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })))

    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText(/作品上下文/)).toBeInTheDocument())
    await user.type(screen.getByRole('textbox', { name: '给司命的消息' }), '调整主角动机')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    const reasoning = await screen.findByRole('button', { name: /模型思考摘要.*已完成/ })
    await user.click(reasoning)
    expect(await screen.findByText('先读取角色档案，再核对当前目标。')).toBeInTheDocument()

    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith(
      '/ai/assistant/conversations/project-conversation-1/turns/assistant-1',
      expect.objectContaining({
        assistant_content: '已调整主角动机',
        status: 'completed',
        scope_type: 'project',
        scope_id: 'project-1',
        run_id: 'run-1',
        operation_id: 'operation-1',
        payload: expect.objectContaining({ reasoning_content: '先读取角色档案，再核对当前目标。' }),
      }),
    ))
  })

  it('sends routed project material as data-only context beside the exact visible instruction', async () => {
    const fileText = '林野🧭来到灰港。'
    const sourceChars = Array.from(fileText).length
    localStorage.setItem('siming.gui.assistant.projectId', 'project-1')
    mockGet.mockImplementation((url: string) => {
      if (url === '/projects') {
        return Promise.resolve({ data: { data: { items: [{ id: 'project-1', title: '测试作品' }], total: 1 } } })
      }
      if (url === '/ai/assistant/conversations') {
        return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      }
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    mockPost.mockImplementation((url: string, body: any) => {
      if (url === '/ai/assistant/conversations') {
        return Promise.resolve({ data: { data: { conversation: { id: 'project-conversation-1', title: '总结附件' } } } })
      }
      if (url === '/ai/assistant/conversations/project-conversation-1/turns/start') {
        expect(body.user_content).toBe('总结附件')
        return Promise.resolve({ data: { data: {
          conversation: { id: 'project-conversation-1', title: '总结附件', scope_type: 'project', scope_id: 'project-1' },
          messages: [{ id: 'user-1' }, { id: 'assistant-1' }],
        } } })
      }
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })
    mockPostForm.mockImplementation((url: string) => {
      if (url !== '/novel-creation/assistant-input/route-file') {
        return Promise.reject(new Error(`unexpected FORM POST ${url}`))
      }
      return Promise.resolve({ data: { data: {
        route: 'reference',
        resolved_instruction: '总结这份内容',
        clarification_question: '',
        source_context: fileText,
        source_coverage: { coverage: 'full', source_chars: sourceChars },
      } } })
    })
    mockAgentTurn.mockImplementation((body: Record<string, unknown>) => {
      expect(body.message).toBe('总结附件')
      expect(body.reference_context).toEqual({
        source_kind: 'attachment',
        source_name: '灰港.txt',
        content: fileText,
        coverage: 'full',
        source_chars: sourceChars,
      })
      return {
        reply: '这段内容讲述林野来到灰港。',
        tool_results: [],
        message_status: 'completed',
      }
    })

    const user = userEvent.setup()
    const { container } = render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText(/作品上下文/)).toBeInTheDocument())
    const upload = await waitFor(() => container.querySelector('input[type="file"]') as HTMLInputElement)
    await user.upload(upload, new File([fileText], '灰港.txt', { type: 'text/plain' }))
    await user.type(screen.getByRole('textbox', { name: '给司命的消息' }), '总结附件')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    expect(await screen.findByText('这段内容讲述林野来到灰港。')).toBeInTheDocument()
    expect(mockAgentTurn).toHaveBeenCalledTimes(1)
  })

  it('offers the free setup flow when no model is configured', async () => {
    modelState.defaultModel = ''
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    expect(await screen.findByText('还差一步：先连接一个模型')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '免费设置' }))
    expect(mockNavigate).toHaveBeenCalledWith('/getting-started')
  })

  it('creates a creation context before the first unbound Agent turn', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '我要创建新的小说')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    expect(await screen.findByText('已按你的要求读取立项数据并启动局部调整。')).toBeInTheDocument()
    await user.click(await screen.findByText('运行过程（2）'))
    expect(await screen.findByText('已准备立项资料能力')).toBeInTheDocument()
    expect(screen.queryByText('set_tool_categories')).not.toBeInTheDocument()
    expect(mockPost).toHaveBeenCalledWith('/novel-creation/start', {
      mode: 'internal_llm',
      user_brief: '',
    })
    expect(mockAgentTurn).toHaveBeenCalledWith(expect.objectContaining({
      session_id: 'session-1',
      message: '我要创建新的小说',
      conversation_id: 'conversation-1',
      assistant_message_id: 'assistant-1',
    }))
    const agentRequest = mockAgentTurn.mock.calls[0]?.[0]
    expect(agentRequest).not.toHaveProperty('history')
    expect(agentRequest).toHaveProperty('client_turn_id')
    expect(mockPatch).not.toHaveBeenCalledWith(
      '/ai/assistant/conversations/conversation-1/turns/assistant-1',
      expect.anything(),
    )
    expect(mockPost).not.toHaveBeenCalledWith('/novel-creation/sessions/session-1/interview/next', expect.anything(), expect.anything())
  })

  it('shows creation checkpoint state outside the transcript and clears it for a new conversation', async () => {
    const result = {
      reply: '已根据整理后的上下文继续立项。',
      tool_results: [],
      message_status: 'completed' as const,
      conversation_id: 'conversation-1',
      assistant_message_id: 'assistant-1',
      turn_persisted: true,
    }
    const events = [
      {
        client_turn_id: 'turn-context', sequence: 1, type: 'turn_started',
        message: '已接收请求', data: { conversation_id: 'conversation-1', assistant_message_id: 'assistant-1' },
      },
      {
        client_turn_id: 'turn-context', sequence: 2, type: 'conversation_context',
        message: '内部上下文状态事件',
        data: { context_state: {
          status: 'compressing', latest_checkpoint_id: 'checkpoint-creation',
          trigger: 'projected_next_step_over_capacity',
        } },
      },
      {
        client_turn_id: 'turn-context', sequence: 3, type: 'conversation_checkpoint',
        message: '内部 checkpoint 详情事件',
        data: { checkpoint: {
          id: 'checkpoint-creation', status: 'ready',
          source_range: { first_sequence: 1, last_sequence: 26, message_count: 26 },
          recent_exact_turn_count: 4,
          original_history_tokens: 72000,
          active_history_tokens: 18000,
          checkpoint_tokens: 5000,
          trigger: 'projected_next_step_over_capacity',
          capacity_assurance: 'exact',
          model_binding: { provider: 'openai', model: 'test' },
          author_quotes: [{
            message_id: 'creation-user-1', exact_quote: '不要改变主角的名字。', purpose: 'active_constraint',
          }],
          execution_ledger: [{
            step_id: 'creation-step-1', tool: 'update_creation_data', status: 'ok',
            detail: '已更新立项资料',
            resource_refs: [{ type: 'creation_session', id: 'session-1', revision: 3 }],
          }],
          semantic_navigation: {
            authority: 'non_authoritative_navigation',
            current_objectives: ['继续完善世界设定'],
          },
          warnings: [],
        } },
      },
      {
        client_turn_id: 'turn-context', sequence: 4, type: 'reply_delta',
        message: '', data: { delta: result.reply },
      },
      {
        client_turn_id: 'turn-context', sequence: 5, type: 'complete',
        message: '本轮完成', data: result,
      },
    ]
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )))

    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '继续完善新书')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    expect(await screen.findByText('已整理较早上下文')).toBeInTheDocument()
    expect(screen.getByText(/保留最近 4 轮原文/)).toBeInTheDocument()
    const transcript = document.querySelector('.gui-chat-messages')
    expect(transcript).not.toHaveTextContent('内部上下文状态事件')
    expect(transcript).not.toHaveTextContent('内部 checkpoint 详情事件')
    expect(transcript).not.toHaveTextContent('已整理较早上下文')

    await user.click(within(screen.getByTestId('conversation-context-notice')).getByRole('button', { name: /查看/ }))
    const dialog = await screen.findByRole('dialog', { name: '上下文整理详情' })
    expect(within(dialog).getByText(/原始 72,000 tokens/)).toBeInTheDocument()
    expect(within(dialog).getByText('不要改变主角的名字。', { exact: false })).toBeInTheDocument()
    expect(within(dialog).getByText('update_creation_data')).toBeInTheDocument()
    expect(within(dialog).getByText(/creation_session · session-1 · r3/)).toBeInTheDocument()
    expect(within(dialog).getByText('继续完善世界设定')).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: '取消整理' })).not.toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: '新建对话' }))
    await waitFor(() => expect(screen.queryByText('已整理较早上下文')).not.toBeInTheDocument())
  })

  it('restores a durable creation checkpoint after reopening the conversation', async () => {
    const baseGet = mockGet.getMockImplementation()
    mockGet.mockImplementation((url: string, ...args: unknown[]) => {
      if (url === '/novel-creation/sessions') {
        return Promise.resolve({ data: { data: { sessions: [{
          id: 'session-restored',
          user_brief: '恢复中的长篇立项',
          status: 'drafting',
          revision: 9,
        }] } } })
      }
      if (url === '/ai/assistant/conversations') {
        return Promise.resolve({ data: { data: { items: [{
          id: 'conversation-restored',
          title: '恢复中的立项对话',
          scope_type: 'creation',
          scope_id: 'session-restored',
          creation_session_id: 'session-restored',
          message_count: 31,
        }], total: 1 } } })
      }
      if (url === '/ai/assistant/conversations/conversation-restored') {
        return Promise.resolve({ data: { data: {
          conversation: {
            id: 'conversation-restored',
            title: '恢复中的立项对话',
            scope_type: 'creation',
            scope_id: 'session-restored',
            creation_session_id: 'session-restored',
          },
          messages: [{
            id: 'assistant-restored',
            conversation_id: 'conversation-restored',
            role: 'assistant',
            content: '上次立项已保存。',
            status: 'completed',
          }],
        } } })
      }
      if (url === '/novel-creation/sessions/session-restored/conversations/conversation-restored/context-state') {
        return Promise.resolve({ data: { data: {
          status: 'ready',
          active_checkpoint_id: 'checkpoint-restored',
          latest_checkpoint_id: 'checkpoint-restored',
          source_range: { first_sequence: 1, last_sequence: 27, message_count: 27 },
          recent_exact_turn_count: 4,
          original_history_tokens: 76000,
          active_history_tokens: 19000,
          checkpoint_tokens: 5200,
          trigger: 'projected_next_step_over_capacity',
          capacity_assurance: 'exact',
          model_binding: { provider: 'openai', model: 'test' },
        } } })
      }
      if (url === '/novel-creation/sessions/session-restored/conversations/conversation-restored/checkpoints/checkpoint-restored') {
        return Promise.resolve({ data: { data: {
          id: 'checkpoint-restored',
          status: 'ready',
          scope: 'creation',
          source_range: { first_sequence: 1, last_sequence: 27, message_count: 27 },
          recent_exact_turn_count: 4,
          original_history_tokens: 76000,
          active_history_tokens: 19000,
          checkpoint_tokens: 5200,
          trigger: 'projected_next_step_over_capacity',
          capacity_assurance: 'exact',
          model_binding: { provider: 'openai', model: 'test' },
          author_quotes: [{
            message_id: 'creation-user-restored',
            exact_quote: '女主必须保留双重身份。',
            purpose: 'active_constraint',
          }],
          execution_ledger: [{
            step_id: 'creation-step-restored',
            tool: 'update_creation_data',
            status: 'ok',
            detail: '已保存角色身份设定',
            resource_refs: [{ type: 'creation_session', id: 'session-restored', revision: 9 }],
          }],
          semantic_navigation: {
            authority: 'non_authoritative_navigation',
            current_objectives: ['继续核对核心冲突'],
          },
          warnings: [],
        } } })
      }
      if (url === '/novel-creation/sessions/session-restored/artifacts') {
        return Promise.resolve({ data: { data: { artifacts: [] } } })
      }
      return baseGet?.(url, ...args)
    })

    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/?conversation=conversation-restored']}>
        <GuiAssistantChat />
      </MemoryRouter>,
    )

    expect(await screen.findByText('上次立项已保存。')).toBeInTheDocument()
    expect(await screen.findByText('已整理较早上下文')).toBeInTheDocument()
    expect(mockGet).toHaveBeenCalledWith(
      '/novel-creation/sessions/session-restored/conversations/conversation-restored/context-state',
    )
    expect(mockGet).toHaveBeenCalledWith(
      '/novel-creation/sessions/session-restored/conversations/conversation-restored/checkpoints/checkpoint-restored',
    )

    await user.click(within(screen.getByTestId('conversation-context-notice')).getByRole('button', { name: /查看/ }))
    const dialog = await screen.findByRole('dialog', { name: '上下文整理详情' })
    expect(within(dialog).getByText('女主必须保留双重身份。', { exact: false })).toBeInTheDocument()
    expect(within(dialog).getByText('继续核对核心冲突')).toBeInTheDocument()
    expect(within(dialog).getByText(/creation_session · session-restored · r9/)).toBeInTheDocument()
    expect(within(dialog).getByText('覆盖时间')).toBeInTheDocument()
    expect(within(dialog).getAllByText('未提供').length).toBeGreaterThan(0)
  })

  it('offers model capacity configuration for an explicit server capacity error', async () => {
    const event = {
      client_turn_id: 'turn-capacity',
      sequence: 1,
      type: 'error',
      message: '模型上下文容量未知',
      data: {
        code: 'conversation_capacity_unknown',
        message: '模型上下文容量未知',
        details: { remediation: 'configure_model_context_profile' },
      },
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      `data: ${JSON.stringify(event)}\n\n`,
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )))
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '继续规划')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    expect(await screen.findByText('需要配置当前模型的上下文容量')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '配置上下文容量' }))
    expect(mockNavigate).toHaveBeenCalledWith('/settings?section=context-governance')
  })

  it('hands a completed formal work off to its project page with the project assistant open', async () => {
    const baseGet = mockGet.getMockImplementation()
    let finalized = false
    mockGet.mockImplementation((url: string, ...args: unknown[]) => {
      if (url === '/projects') {
        return Promise.resolve({ data: { data: {
          items: finalized ? [{ id: 'formal-project-1', title: '灰港遗忘症' }] : [],
          total: finalized ? 1 : 0,
        } } })
      }
      if (url === '/novel-creation/sessions') {
        return Promise.resolve({ data: { data: { sessions: finalized ? [{
          id: 'session-1', status: 'completed', revision: 8,
          display_title: '灰港遗忘症', created_project_id: 'formal-project-1',
        }] : [] } } })
      }
      return baseGet?.(url, ...args)
    })
    mockAgentTurn.mockImplementation(() => {
      finalized = true
      return {
        reply: '项目建好了，我们继续在这里写第一章。',
        run: null,
        tool_results: [{
          tool: 'finalize_creation_session', status: 'ok', data: { project_id: 'formal-project-1' },
        }],
        created_project_id: 'formal-project-1',
        message_status: 'completed',
        conversation_id: 'conversation-1',
        assistant_message_id: 'assistant-1',
        turn_persisted: true,
      }
    })

    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '确认创建正式作品')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    await waitFor(() => expect(mockAgentTurn).toHaveBeenCalledWith(
      expect.objectContaining({ session_id: 'session-1' }),
    ))
    expect(await screen.findByText(/正式作品已创建并进入作品库/)).toBeInTheDocument()
    const handoff = await screen.findByRole('button', { name: '进入正式作品并展开项目助手' })
    expect(screen.getByText('灰港遗忘症')).toBeInTheDocument()
    expect(screen.queryByText('项目建好了，我们继续在这里写第一章。')).not.toBeInTheDocument()
    expect(screen.queryByText('打开完整编辑器')).not.toBeInTheDocument()
    await user.click(handoff)
    expect(mockNavigate).toHaveBeenCalledWith('/project/formal-project-1?assistant=open')
  })

  it('does not render deleted works, their creation data, or their conversations anywhere', async () => {
    localStorage.setItem('siming.gui.assistant.sidebarCollapsed', '0')
    localStorage.setItem('siming.gui.assistant.projectId', 'deleted-project')
    mockGet.mockImplementation((url: string, params?: Record<string, unknown>) => {
      if (url === '/projects') return Promise.resolve({ data: { data: {
        items: [{ id: 'live-project', title: '仍在作品库' }], total: 1,
      } } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [
        {
          id: 'deleted-session', status: 'completed', revision: 9,
          display_title: '已经删除的作品', created_project_id: 'deleted-project',
        },
        {
          id: 'deleted-source-session', status: 'drafting', revision: 4,
          display_title: '已删除作品的立项资料', source_project_id: 'deleted-project',
        },
        {
          id: 'active-session', status: 'drafting', revision: 3,
          display_title: '仍在筹备', created_project_id: null,
        },
        {
          id: 'live-source-session', status: 'drafting', revision: 2,
          display_title: '仍有效的作品立项', source_project_id: 'live-project',
        },
      ] } } })
      if (url === '/ai/assistant/conversations' && params?.scope_type === 'creation') {
        return Promise.resolve({ data: { data: { items: [
          {
            id: 'deleted-conversation', title: '已删除作品的对话', scope_type: 'creation',
            scope_id: 'deleted-session', creation_session_id: 'deleted-session',
          },
          {
            id: 'deleted-source-conversation', title: '已删除作品的立项对话', scope_type: 'creation',
            scope_id: 'deleted-source-session', creation_session_id: 'deleted-source-session',
          },
          {
            id: 'active-conversation', title: '保留的立项对话', scope_type: 'creation',
            scope_id: 'active-session', creation_session_id: 'active-session',
          },
          {
            id: 'live-source-conversation', title: '保留的作品立项对话', scope_type: 'creation',
            scope_id: 'live-source-session', creation_session_id: 'live-source-session',
          },
        ], total: 4 } } })
      }
      if (url === '/ai/assistant/conversations') {
        return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      }
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })

    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/?creationSession=deleted-source-session']}>
        <GuiAssistantChat />
      </MemoryRouter>,
    )

    expect(await screen.findByText('保留的立项对话')).toBeInTheDocument()
    expect(localStorage.getItem('siming.gui.assistant.projectId')).toBeNull()
    expect(screen.getByText('保留的作品立项对话')).toBeInTheDocument()
    expect(screen.queryByText('已删除作品的对话')).not.toBeInTheDocument()
    expect(screen.queryByText('已删除作品的立项对话')).not.toBeInTheDocument()
    expect(screen.queryByRole('complementary', { name: '作品资料' })).not.toBeInTheDocument()
    expect(mockGet).not.toHaveBeenCalledWith('/novel-creation/sessions/deleted-source-session/artifacts')
    await user.click(screen.getByRole('combobox', { name: '选择作品上下文' }))
    expect(await screen.findByText('仍在筹备 · 筹备中')).toBeInTheDocument()
    expect(screen.getByText('仍有效的作品立项 · 筹备中')).toBeInTheDocument()
    expect(screen.queryByText('已经删除的作品 · 筹备中')).not.toBeInTheDocument()
    expect(screen.queryByText('已删除作品的立项资料 · 筹备中')).not.toBeInTheDocument()
  })

  it('keeps structured creation data visible beside the conversation and confirms it in place', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '我要创建新的小说')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    expect(await screen.findByRole('complementary', { name: '作品资料' })).toBeInTheDocument()
    expect(await screen.findByText('角色与关系')).toBeInTheDocument()
    expect(screen.getByText('上游创意方案已修改')).toBeInTheDocument()
    expect(screen.getByText('已锁定 1 项')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '确认创意方案' }))
    expect(mockPost).toHaveBeenCalledWith('/novel-creation/sessions/session-1/stages/concepts/confirm', {
      confirm: true,
      source: 'author',
      expected_revision: 7,
    })

    await user.click(screen.getByRole('button', { name: '撤销创意方案最近一次修改' }))
    expect(mockPost).toHaveBeenCalledWith('/novel-creation/sessions/session-1/artifacts/concepts/undo', {
      expected_revision: 7,
    })
  })

  it('uses the confirmed artifact as the source of truth over a stale waiting task snapshot', async () => {
    const baseGet = mockGet.getMockImplementation()
    mockGet.mockImplementation((url: string, ...args: unknown[]) => {
      if (url === '/novel-creation/sessions/session-1/artifacts') {
        return Promise.resolve({ data: { data: {
          revision: 8,
          artifacts: [{
            artifact: 'concepts',
            label: '创意方案',
            status: 'confirmed',
            stored_status: 'confirmed',
            source: 'author',
            revision: 8,
            locked_paths: [],
            flow: { can_confirm: false, soft_dependencies: [] },
          }],
        } } })
      }
      return baseGet?.(url, ...args)
    })
    mockAgentTurn.mockImplementation(() => ({
      reply: '创意方案已生成。',
      run: {
        id: 'run-waiting',
        session_id: 'session-1',
        stage: 'concepts',
        status: 'waiting_user',
        operation_id: 'operation-waiting',
        current_message: '阶段结果已保存，等待作者确认',
      },
      tool_results: [],
      message_status: 'completed',
      conversation_id: 'conversation-1',
      assistant_message_id: 'assistant-1',
      turn_persisted: true,
    }))

    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '我要创建新的小说')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    expect(await screen.findByText('阶段内容已由作者确认，立项数据和任务状态已同步。')).toBeInTheDocument()
    expect(screen.queryByText('等待确认')).not.toBeInTheDocument()
  })

  it('uses the selected model presentation when a raw failed run actually saved the artifact', async () => {
    const baseGet = mockGet.getMockImplementation()
    const basePost = mockPost.getMockImplementation()
    mockGet.mockImplementation((url: string, ...args: unknown[]) => {
      if (url === '/novel-creation/sessions/session-1/artifacts') {
        return Promise.resolve({ data: { data: {
          revision: 8,
          artifacts: [{
            artifact: 'characters',
            label: '角色与关系',
            status: 'generated',
            stored_status: 'generated',
            source: 'assistant',
            revision: 8,
            locked_paths: [],
            flow: { can_confirm: true, soft_dependencies: [] },
          }],
        } } })
      }
      return baseGet?.(url, ...args)
    })
    mockAgentTurn.mockImplementation(() => ({
      reply: '同时写了 4 条关系，其余内容未改动。',
      run: {
        id: 'run-characters-failed',
        session_id: 'session-1',
        stage: 'characters',
        status: 'failed',
        operation_id: 'operation-characters-failed',
        current_message: '请先选择一个创意方向',
      },
      tool_results: [],
      message_status: 'completed',
      conversation_id: 'conversation-1',
      assistant_message_id: 'assistant-1',
      turn_persisted: true,
    }))
    mockPost.mockImplementation((url: string, ...args: unknown[]) => {
      if (url === '/novel-creation/runs/run-characters-failed/card-presentation') {
        return Promise.resolve({ data: { data: { run: {
          id: 'run-characters-failed',
          session_id: 'session-1',
          stage: 'characters',
          status: 'failed',
          operation_id: 'operation-characters-failed',
          current_message: '请先选择一个创意方向',
          card_presentation: {
            status: 'waiting_user',
            label: '等待确认',
            message: '角色与关系已经写入作品资料，当前等待你确认。',
            show_retry: false,
            judged_by: 'model',
            raw_status: 'failed',
          },
        } } } })
      }
      return basePost?.(url, ...args)
    })

    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    const input = await screen.findByRole('textbox', { name: '给司命的消息' })
    await user.type(input, '我要创建一本新的小说')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    expect(await screen.findByText('角色与关系已经写入作品资料，当前等待你确认。')).toBeInTheDocument()
    expect(screen.getByText('等待确认')).toBeInTheDocument()
    expect(screen.queryByText('失败')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /按原输入重试/ })).not.toBeInTheDocument()
    expect(mockPost).toHaveBeenCalledWith(
      '/novel-creation/runs/run-characters-failed/card-presentation',
      expect.objectContaining({ model: 'openai:test' }),
      { timeout: 0 },
    )
  })

  it('shows a preserved candidate when an old task conflicts with newer author data', async () => {
    const baseGet = mockGet.getMockImplementation()
    mockGet.mockImplementation((url: string, ...args: unknown[]) => {
      if (url === '/novel-creation/sessions/session-1/artifacts') {
        return Promise.resolve({ data: { data: {
          revision: 9,
          artifacts: [{
            artifact: 'characters',
            label: '角色与关系',
            status: 'conflict',
            stored_status: 'confirmed',
            source: 'author',
            revision: 9,
            locked_paths: ['/characters/0'],
            conflict: {
              run_id: 'run-conflict',
              message: '任务基于版本 7，当前版本为 9',
              candidate_available: true,
              input_revision: 7,
              current_revision: 9,
            },
            flow: { can_confirm: false, soft_dependencies: [] },
          }],
        } } })
      }
      return baseGet?.(url, ...args)
    })

    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '我要创建新的小说')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    expect(await screen.findByText('版本冲突')).toBeInTheDocument()
    expect(screen.getByText('旧任务结果未覆盖当前内容；候选稿已保留，可按原输入或最新内容重试')).toBeInTheDocument()
  })

  it('shows immutable artifact history, compares revisions, and restores with revision protection', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    await user.type(await screen.findByRole('textbox', { name: '\u7ed9\u53f8\u547d\u7684\u6d88\u606f' }), '\u6211\u8981\u521b\u5efa\u65b0\u7684\u5c0f\u8bf4')
    await user.click(screen.getByRole('button', { name: /\u53d1\u9001/ }))

    await user.click(await screen.findByRole('button', { name: '\u67e5\u770b\u521b\u610f\u65b9\u6848\u7248\u672c\u5386\u53f2' }))
    expect(await screen.findByRole('dialog', { name: /\u7248\u672c\u5386\u53f2/ })).toBeInTheDocument()
    expect(await screen.findByText('/concepts/0/title')).toBeInTheDocument()
    expect(mockGet).toHaveBeenCalledWith('/novel-creation/artifact-versions/version-new')

    await user.click(screen.getAllByRole('listitem')[1])
    expect(await screen.findByText('/concepts')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /\u6062\u590d\u6b64\u7248\u672c/ }))

    expect(mockPost).toHaveBeenCalledWith('/novel-creation/artifact-versions/version-old/restore', {
      expected_revision: 7,
    })
  })

  it('makes the current conversation model visible', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    await user.click(await screen.findByRole('button', { name: '查看当前模型与运行状态' }))
    expect(await screen.findByRole('combobox', { name: '选择本次对话模型' })).toBeInTheDocument()
    expect(screen.getByText('OpenAI · test')).toBeInTheDocument()
  })

  it('starts a targeted artifact refinement from chat without opening the workbench', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    const input = await screen.findByRole('textbox', { name: '给司命的消息' })
    await user.type(input, '我要创建一本新的小说')
    await user.click(screen.getByRole('button', { name: /发送/ }))
    expect(await screen.findByText('立项任务')).toBeInTheDocument()

    await user.type(input, '主角保持不变，重做反派和人物关系')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    await waitFor(() => {
      expect(mockAgentTurn).toHaveBeenCalledWith(expect.objectContaining({
        session_id: 'session-1',
        message: '主角保持不变，重做反派和人物关系',
      }))
    })
    expect(screen.getAllByText('已按你的要求读取立项数据并启动局部调整。')).toHaveLength(2)
    expect(mockNavigate).not.toHaveBeenCalledWith(expect.stringContaining('/novel-creation'))
  })

  it('lets the model infer entity count from the full instruction instead of regenerating the whole cast', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    const input = await screen.findByRole('textbox', { name: '给司命的消息' })
    await user.type(input, '我要创建一本新的小说')
    await user.click(screen.getByRole('button', { name: /发送/ }))
    await user.type(input, '创建两个新的反派角色')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    await waitFor(() => {
      expect(mockAgentTurn).toHaveBeenCalledWith(expect.objectContaining({
        session_id: 'session-1',
        message: '创建两个新的反派角色',
      }))
    })
  })

  it('shows structured creation data in chat without requiring the workbench', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    const input = await screen.findByRole('textbox', { name: '给司命的消息' })
    await user.type(input, '我要创建一本新的小说')
    await user.click(screen.getByRole('button', { name: /发送/ }))
    const panel = await screen.findByRole('complementary', { name: '作品资料' })
    await user.click((await screen.findAllByRole('button', { name: /进入编辑器/ }))[0])

    expect(await screen.findByRole('heading', { name: '创意方案' })).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: /灰港遗忘症/ }))
    expect(await screen.findByDisplayValue('灰港遗忘症')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /返回作品资料/ })).toBeInTheDocument()
    expect(panel).toBeInTheDocument()
  })

  it('does not reopen a structured editor after its detail response arrives late', async () => {
    const artifactDetail = deferred<{ data: { data: {
      artifact: string
      label: string
      status: string
      source: string
      revision: number
      data: Record<string, unknown>
    } } }>()
    const baseGet = mockGet.getMockImplementation()
    mockGet.mockImplementation((url: string, ...args: unknown[]) => {
      if (url === '/novel-creation/sessions/session-1/artifacts/concepts') return artifactDetail.promise
      return baseGet?.(url, ...args)
    })

    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '我要创建新的小说')
    await user.click(screen.getByRole('button', { name: /发送/ }))
    await user.click((await screen.findAllByRole('button', { name: /进入编辑器/ }))[0])
    expect(await screen.findByRole('heading', { name: '创意方案' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /返回作品资料/ }))
    expect(screen.queryByRole('heading', { name: '创意方案', level: 3 })).not.toBeInTheDocument()

    await act(async () => {
      artifactDetail.resolve({ data: { data: {
        artifact: 'concepts', label: '创意方案', status: 'generated', source: 'model', revision: 7,
        data: { options: [{ title: '迟到的旧方案' }] },
      } } })
      await artifactDetail.promise
    })

    expect(screen.queryByRole('heading', { name: '创意方案', level: 3 })).not.toBeInTheDocument()
    expect(screen.queryByText('迟到的旧方案')).not.toBeInTheDocument()
  })

  it('keeps the structured editor open when saving before a new conversation fails', async () => {
    localStorage.setItem('siming.gui.assistant.sidebarCollapsed', '0')
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    await user.type(await screen.findByRole('textbox', { name: '给司命的消息' }), '我要创建新的小说')
    await user.click(screen.getByRole('button', { name: /发送/ }))
    await user.click((await screen.findAllByRole('button', { name: /进入编辑器/ }))[0])
    await user.click(await screen.findByRole('button', { name: /灰港遗忘症/ }))

    const title = await screen.findByDisplayValue('灰港遗忘症')
    await user.clear(title)
    await user.type(title, '尚未保存的创意')
    mockPatch.mockRejectedValueOnce(new Error('模拟保存失败'))

    await user.click((await screen.findAllByRole('button', { name: '新对话' }))[0])

    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith(
      '/novel-creation/sessions/session-1/artifacts/concepts',
      expect.objectContaining({ expected_revision: 7 }),
    ))
    expect(screen.getByRole('heading', { name: '创意方案' })).toBeInTheDocument()
    expect(screen.getByDisplayValue('尚未保存的创意')).toBeInTheDocument()
    expect(await screen.findByText('模拟保存失败')).toBeInTheDocument()
  })

  it('keeps a new conversation empty when the previous history response arrives late', async () => {
    localStorage.setItem('siming.gui.assistant.sidebarCollapsed', '0')
    const history = deferred<{ data: { data: {
      conversation: { id: string; title: string; scope_type: string; creation_session_id?: string }
      messages: Array<{ id: string; conversation_id: string; role: 'user' | 'assistant'; content: string }>
    } } }>()
    mockGet.mockImplementation((url: string) => {
      if (url === '/projects') return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [{
        id: 'session-old', user_brief: '旧立项', status: 'drafting', revision: 1,
      }] } } })
      if (url === '/ai/assistant/conversations') return Promise.resolve({ data: { data: { items: [{
        id: 'conversation-old', title: '旧对话', scope_type: 'creation', creation_session_id: 'session-old', message_count: 1,
      }], total: 1 } } })
      if (url === '/ai/assistant/conversations/conversation-old') return history.promise
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })

    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={['/?conversation=conversation-old']}><GuiAssistantChat /></MemoryRouter>)
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith('/ai/assistant/conversations/conversation-old'))

    await user.click((await screen.findAllByRole('button', { name: '新对话' }))[0])
    await act(async () => {
      history.resolve({ data: { data: {
        conversation: { id: 'conversation-old', title: '旧对话', scope_type: 'creation', creation_session_id: 'session-old' },
        messages: [{ id: 'old-message', conversation_id: 'conversation-old', role: 'assistant', content: '迟到的旧消息' }],
      } } })
      await history.promise
    })

    expect(screen.queryByText('迟到的旧消息')).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '给司命的消息' })).toHaveValue('')
  })

  it('keeps the focused structured editor visible when the window becomes compact', async () => {
    const mediaListeners = new Set<(event: MediaQueryListEvent) => void>()
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: false,
      addEventListener: (_event: string, listener: (event: MediaQueryListEvent) => void) => mediaListeners.add(listener),
      removeEventListener: (_event: string, listener: (event: MediaQueryListEvent) => void) => mediaListeners.delete(listener),
    }))
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    const input = await screen.findByRole('textbox', { name: '给司命的消息' })
    await user.type(input, '我要创建一本新的小说')
    await user.click(screen.getByRole('button', { name: /发送/ }))
    const panel = await screen.findByRole('complementary', { name: '作品资料' })
    await user.click((await screen.findAllByRole('button', { name: /进入编辑器/ }))[0])
    expect(await screen.findByRole('heading', { name: '创意方案' })).toBeInTheDocument()

    act(() => {
      for (const listener of mediaListeners) {
        listener({ matches: true } as MediaQueryListEvent)
      }
    })

    expect(panel).toHaveClass('gui-chat-creation-panel-open')
    expect(panel).toHaveClass('gui-chat-creation-panel-editor-open')
    expect(screen.getByRole('heading', { name: '创意方案' })).toBeVisible()
  })

  it('uploads creation material as a durable binary import and applies a selected preview', async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === '/projects') return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      if (url === '/ai/assistant/conversations') return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      if (url === '/novel-creation/sessions/session-1/imports') return Promise.resolve({ data: { data: { imports: [] } } })
      if (url === '/novel-creation/sessions/session-1/artifacts') return Promise.resolve({ data: { data: { artifacts: [{ artifact: 'characters', label: '角色与关系', status: 'pending', revision: 1 }] } } })
      if (url === '/novel-creation/imports/import-1') return Promise.resolve({ data: { data: {
        id: 'import-1', source_file_id: 'import-1', session_id: 'session-1', operation_id: 'operation-import-1',
        filename: '八卷大纲.md', status: 'waiting_user', text_length: 32000, chunk_count: 5, processed_chunks: 5, input_revision: 1,
        preview: {
          detected: { characters: 12, factions: 4, locations: 19, volumes: 8, chapter_summaries: 146 },
          artifact_counts: { characters: 12, locations: 23, macro_outline: 8, opening_outline: 146 },
          available_artifacts: ['characters', 'locations', 'macro_outline', 'opening_outline'],
          conflicts: [{ kind: 'existing_artifact', artifact: 'characters', status: 'generated' }],
        },
      } } })
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    mockPost.mockImplementation((url: string) => {
      if (url === '/novel-creation/start') return Promise.resolve({ data: { data: { session_id: 'session-1' } } })
      if (url === '/novel-creation/imports/import-1/apply') return Promise.resolve({ data: { data: {
        applied: [{ artifact: 'characters', count: 12 }, { artifact: 'macro_outline', count: 8 }], skipped: [], revision: 3,
      } } })
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })
    mockPostForm.mockImplementation((url: string, body: unknown) => {
      if (url === '/novel-creation/assistant-input/route-file') {
        expect(body).toBeInstanceOf(FormData)
        return Promise.resolve({ data: { data: {
          route: 'creation_material', resolved_instruction: '整理为作品大纲资料',
          clarification_question: '', source_context: '# 第一卷\n卷纲内容',
          source_coverage: { coverage: 'full', source_chars: 11, included_chars: 11, omitted_chars: 0 },
        } } })
      }
      if (url === '/novel-creation/sessions/session-1/imports') {
        expect(body).toBeInstanceOf(FormData)
        return Promise.resolve({ data: { data: {
          id: 'import-1', source_file_id: 'import-1', session_id: 'session-1', operation_id: 'operation-import-1',
          filename: '八卷大纲.md', status: 'running', text_length: 0, chunk_count: 0, processed_chunks: 0, input_revision: 1,
        } } })
      }
      return Promise.reject(new Error(`unexpected FORM POST ${url}`))
    })

    const user = userEvent.setup()
    const { container } = render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    const upload = await waitFor(() => container.querySelector('input[type="file"]') as HTMLInputElement)
    const file = new File(['# 第一卷\n卷纲内容'], '八卷大纲.md', { type: 'text/markdown' })
    await user.upload(upload, file)
    expect((await screen.findAllByText(/八卷大纲.md/)).length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: /发送/ }))

    expect(await screen.findByText('资料导入')).toBeInTheDocument()
    expect(await screen.findByText('卷纲 8')).toBeInTheDocument()
    expect(await screen.findByText('导入预览 · 八卷大纲.md')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '应用所选数据' }))
    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/novel-creation/imports/import-1/apply', expect.objectContaining({
        selected_artifacts: expect.arrayContaining(['characters', 'macro_outline']),
        strategy: 'merge',
        expected_revision: 1,
      }))
    })
    expect(await screen.findByText(/导入已完成/)).toBeInTheDocument()
  })

  it('accepts up to one million characters and turns long creation text into a durable import', async () => {
    const longText = `长篇设定：${'宗门与人物关系。'.repeat(2500)}`
    mockPost.mockImplementation((url: string, body: any) => {
      if (url === '/novel-creation/assistant-input/route') {
        expect(body.source_text).toBe(longText)
        expect(body.user_instruction).toBe('')
        expect(body).not.toHaveProperty('history')
        return Promise.resolve({ data: { data: {
          route: 'creation_material', resolved_instruction: '整理成长篇作品设定',
          clarification_question: '', source_context: longText.slice(0, 16000),
          source_coverage: { coverage: 'distributed', source_chars: longText.length, included_chars: 16000 },
        } } })
      }
      if (url === '/novel-creation/start') return Promise.resolve({ data: { data: { session_id: 'session-1' } } })
      if (url === '/ai/assistant/conversations') return Promise.resolve({ data: { data: { conversation: { id: 'conversation-1', title: '长文本' } } } })
      if (url === '/ai/assistant/conversations/conversation-1/turns/start') {
        expect(body.user_content).toBe(longText)
        return Promise.resolve({ data: { data: {
          conversation: { id: 'conversation-1', title: '长文本' },
          messages: [{ id: 'user-long' }, { id: 'assistant-long' }],
        } } })
      }
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })
    mockPostForm.mockImplementation((url: string, body: unknown) => {
      if (url === '/novel-creation/sessions/session-1/imports') {
        expect(body).toBeInstanceOf(FormData)
        return Promise.resolve({ data: { data: {
          id: 'import-long', source_file_id: 'file-long', session_id: 'session-1', operation_id: 'operation-long',
          filename: '聊天长文本.txt', status: 'running', text_length: longText.length,
          chunk_count: 3, processed_chunks: 0, input_revision: 1,
        } } })
      }
      return Promise.reject(new Error(`unexpected FORM POST ${url}`))
    })

    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    const input = await screen.findByRole('textbox', { name: '给司命的消息' })
    expect(input).toHaveAttribute('maxlength', '1000000')
    fireEvent.change(input, { target: { value: longText } })
    await user.click(screen.getByRole('button', { name: /发送/ }))

    expect(await screen.findByText(/已提交长文本/)).toHaveTextContent(longText.length.toLocaleString('zh-CN'))
    await waitFor(() => expect(mockPostForm).toHaveBeenCalledWith(
      '/novel-creation/sessions/session-1/imports', expect.any(FormData), { timeout: 0 },
    ))
  })

  it('reads an instruction embedded in a TXT before choosing how to handle it', async () => {
    const fileText = '给司命的要求：请把以下设定整理到当前作品资料中。\n\n角色：林野。'
    mockPost.mockImplementation((url: string) => {
      if (url === '/novel-creation/start') return Promise.resolve({ data: { data: { session_id: 'session-1' } } })
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })
    mockPostForm.mockImplementation((url: string, body: FormData) => {
      if (url === '/novel-creation/assistant-input/route-file') {
        expect(body.get('user_instruction')).toBe('')
        expect(body.get('file')).toBeInstanceOf(File)
        expect(body.get('history')).toBeNull()
        return Promise.resolve({ data: { data: {
          route: 'creation_material', resolved_instruction: '把设定整理到当前作品资料中',
          clarification_question: '', source_context: fileText,
          source_coverage: { coverage: 'full', source_chars: fileText.length, included_chars: fileText.length },
        } } })
      }
      if (url === '/novel-creation/sessions/session-1/imports') {
        return Promise.resolve({ data: { data: {
          id: 'import-embedded', source_file_id: 'file-embedded', session_id: 'session-1', operation_id: 'operation-embedded',
          filename: '角色设定.txt', status: 'running', text_length: fileText.length,
          chunk_count: 1, processed_chunks: 0, input_revision: 1,
        } } })
      }
      return Promise.reject(new Error(`unexpected FORM POST ${url}`))
    })

    const user = userEvent.setup()
    const { container } = render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    const upload = await waitFor(() => container.querySelector('input[type="file"]') as HTMLInputElement)
    await user.upload(upload, new File([fileText], '角色设定.txt', { type: 'text/plain' }))
    await user.click(screen.getByRole('button', { name: /发送/ }))

    await waitFor(() => expect(mockPostForm).toHaveBeenCalledWith(
      '/novel-creation/assistant-input/route-file', expect.any(FormData), { timeout: 0 },
    ))
    await waitFor(() => expect(mockPostForm).toHaveBeenCalledWith(
      '/novel-creation/sessions/session-1/imports', expect.any(FormData), { timeout: 0 },
    ))
  })

  it('can ask more than once without losing the original TXT', async () => {
    const fileText = '林野来到灰港。'
    let routeCalls = 0
    mockAgentTurn.mockImplementation((body: Record<string, unknown>) => {
      expect(body.message).toBe('总结')
      expect(body.reference_context).toEqual({
        source_kind: 'attachment',
        source_name: '灰港.txt',
        content: fileText,
        coverage: 'full',
        source_chars: fileText.length,
      })
      expect(String(body.message || '')).not.toContain(fileText)
      expect(JSON.stringify(body.reference_context)).not.toContain('总结这份内容')
      return {
        reply: '这段内容讲述林野来到灰港。',
        run: null,
        tool_results: [],
        message_status: 'completed',
        conversation_id: 'conversation-1',
        assistant_message_id: 'assistant-1',
        turn_persisted: true,
      }
    })
    mockPostForm.mockImplementation((url: string, body: FormData) => {
      if (url !== '/novel-creation/assistant-input/route-file') {
        return Promise.reject(new Error(`unexpected FORM POST ${url}`))
      }
      routeCalls += 1
      expect((body.get('file') as File).name).toBe('灰港.txt')
      if (routeCalls === 1) {
        return Promise.resolve({ data: { data: {
          route: 'clarify', resolved_instruction: '', clarification_question: '你想分析它，还是写入作品资料？',
          source_context: fileText, source_coverage: { coverage: 'full', source_chars: fileText.length },
        } } })
      }
      const clarificationHistory = JSON.parse(String(body.get('clarification_history')))
      if (routeCalls === 2) {
        expect(clarificationHistory).toEqual([
          { question: '你想分析它，还是写入作品资料？', answer: '分析一下' },
        ])
        return Promise.resolve({ data: { data: {
          route: 'clarify', resolved_instruction: '', clarification_question: '你更需要总结，还是文学点评？',
          source_context: fileText, source_coverage: { coverage: 'full', source_chars: fileText.length },
        } } })
      }
      expect(clarificationHistory).toEqual([
        { question: '你想分析它，还是写入作品资料？', answer: '分析一下' },
        { question: '你更需要总结，还是文学点评？', answer: '总结' },
      ])
      return Promise.resolve({ data: { data: {
        route: 'reference', resolved_instruction: '总结这份内容', clarification_question: '',
        source_context: fileText, source_coverage: { coverage: 'full', source_chars: fileText.length },
      } } })
    })

    const user = userEvent.setup()
    const { container } = render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)
    const upload = await waitFor(() => container.querySelector('input[type="file"]') as HTMLInputElement)
    await user.upload(upload, new File([fileText], '灰港.txt', { type: 'text/plain' }))
    await user.click(screen.getByRole('button', { name: /发送/ }))
    expect(await screen.findByText('你想分析它，还是写入作品资料？')).toBeInTheDocument()

    const input = screen.getByRole('textbox', { name: '给司命的消息' })
    await user.type(input, '分析一下')
    await user.click(screen.getByRole('button', { name: /发送/ }))
    expect(await screen.findByText('你更需要总结，还是文学点评？')).toBeInTheDocument()
    expect(screen.getByText(/原始内容和此前回答已保留/)).toBeInTheDocument()

    await user.type(input, '总结')
    await user.click(screen.getByRole('button', { name: /发送/ }))
    expect(await screen.findByText('这段内容讲述林野来到灰港。')).toBeInTheDocument()
    expect(routeCalls).toBe(3)
  })

  it('does not treat a pasted local path as consent and asks for a read-only snapshot', async () => {
    modelState.defaultModel = 'opencode_cli:opencode/big-pickle'
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    await user.type(
      await screen.findByRole('textbox', { name: '给司命的消息' }),
      '请读取 "C:\\Novel Notes\\人物设定.md"',
    )
    await user.click(screen.getByRole('button', { name: /发送/ }))

    expect(await screen.findByRole('dialog', { name: '仅允许 OpenCode 读取这些路径一次？' })).toBeInTheDocument()
    expect(screen.getByText('C:\\Novel Notes\\人物设定.md')).toBeInTheDocument()
    expect(screen.getByText(/路径文字本身不会被当作授权/)).toBeInTheDocument()
    expect(screen.getByText(/不能访问原路径、父目录或相邻文件/)).toBeInTheDocument()
    expect(mockAgentTurn).not.toHaveBeenCalled()
  })
})
