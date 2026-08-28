import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

const { mockGet, mockPost, mockPatch, mockDelete, mockNavigate, modelState } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockPatch: vi.fn(),
  mockDelete: vi.fn(),
  mockNavigate: vi.fn(),
  modelState: { hasModels: true },
}))

vi.mock('../api/client', () => ({
  apiClient: { get: mockGet, post: mockPost, patch: mockPatch, delete: mockDelete },
}))

vi.mock('../hooks/useModelOptions', () => ({
  useModelOptions: () => ({
    modelOptions: modelState.hasModels ? [{ value: 'openai:test', label: 'OpenAI · test' }] : [],
    defaultModel: modelState.hasModels ? 'openai:test' : undefined,
    hasModels: modelState.hasModels,
    loading: false,
  }),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

import NovelCreationWizardPage from '../pages/NovelCreationWizardPage'

const presets = {
  categories: [
    {
      id: 'xuanhuan', label: '玄幻奇幻', description: '升级与世界奇观',
      themes: [{ id: 'xuanhuan:1', label: '东方玄幻' }],
      defaults: { world_tone: '奇观有代价', story_structure: '成长双线', pacing: '三章一钩', writing_style: '动作明确', special_requirements: ['状态更新'], avoid: ['境界刷屏'] },
    },
    {
      id: 'suspense', label: '悬疑推理', description: '证据链与认知差',
      themes: [{ id: 'suspense:1', label: '社会派悬疑' }],
      defaults: { world_tone: '信息公平', story_structure: '三层谜团', pacing: '证据推进', writing_style: '精确克制', special_requirements: ['证据可回看'], avoid: ['空降凶手'] },
    },
  ],
  platforms: ['暂不确定'], audiences: ['成年大众'],
  length_options: [{ id: 'long', label: '长篇', words: 600000, chapters: 240 }],
  stage_order: ['constraints', 'concepts', 'world_style', 'characters', 'locations', 'macro_outline', 'opening_outline', 'final_review'],
  stage_labels: {
    world_style: '文风与世界观', characters: '角色与关系', locations: '地点与势力',
    macro_outline: '全书主线与卷纲', opening_outline: '前3章细纲', final_review: '最终审阅',
  },
}

function renderPage(path = '/novel-creation') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/novel-creation" element={<NovelCreationWizardPage />} /></Routes>
    </MemoryRouter>,
  )
}

describe('NovelCreationWizardPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    modelState.hasModels = true
    mockGet.mockImplementation((url: string) => {
      if (url === '/novel-creation/presets') return Promise.resolve({ data: { data: presets } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [] } } })
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
  })

  it('shows editable genre presets and applies the selected profile', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('新书立项工作台')
    await user.click(screen.getByRole('button', { name: /帮我探索创意/ }))
    await user.click(screen.getByRole('button', { name: /悬疑推理/ }))
    await user.click(screen.getByText('创作约束与高级设置'))
    expect(await screen.findByDisplayValue('信息公平')).toBeInTheDocument()
    expect(screen.getByDisplayValue('空降凶手')).toBeInTheDocument()
  })

  it('uses the only ready model directly and exposes the mobile genre-scroll hint', async () => {
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText('AI 已准备好')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /帮我探索创意/ }))
    expect(screen.queryByRole('combobox', { name: '选择本阶段模型' })).not.toBeInTheDocument()
    expect(screen.getByText('选择题材')).toBeInTheDocument()
    expect(screen.getByText('左右滑动选择')).toBeInTheDocument()
  })

  it('allows saving the intake but explains model setup when none is configured', async () => {
    const user = userEvent.setup()
    modelState.hasModels = false
    renderPage()
    expect(await screen.findByText('当前没有可用模型')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /按我的设定立项/ }))
    expect(screen.getByRole('button', { name: /只保存草稿/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: '免费开始' })).toBeInTheDocument()
  })

  it('opens the workbench without requiring a concept selection', async () => {
    const session = {
      id: 'session-1', status: 'reviewing', revision: 2, current_stage: 'concepts',
      draft: {
        form: { brief: '记忆病毒', preset_id: 'suspense', genre: '悬疑推理', target_audience: '成年大众', platform: '暂不确定', target_words: 600000, target_chapters: 240, world_tone: '信息公平', story_structure: '三层谜团', pacing: '证据推进', writing_style: '精确克制', special_requirements: [], avoid: [] },
        concepts: [{ id: 'concept-1', source_index: 0, title: '灰港遗忘症', logline: '女孩用遗忘换取感染者的记忆。', protagonist_seed: { name: '林七', identity: '医生', goal: '找母亲', lack: '害怕遗忘' }, world_hook: '记忆传播', core_conflict: '救人就会遗忘', story_engine: '读忆换线索', opening_hook: '陌生人说出她的童年', differentiators: ['记忆感染'], risks: ['规则需稳定'], coverage: { score: 92, covered: [], missing: [] } }],
        stages: {},
      },
    }
    mockGet.mockImplementation((url: string) => {
      if (url === '/novel-creation/presets') return Promise.resolve({ data: { data: presets } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [session] } } })
      if (url === '/novel-creation/sessions/session-1') return Promise.resolve({ data: { data: session } })
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    renderPage('/novel-creation?session=session-1')
    expect(await screen.findByRole('heading', { name: '文风与世界观' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '确认当前方向' })).not.toBeInTheDocument()
  })

  it('reconnects to the active lightweight-concept run after a handoff', async () => {
    const user = userEvent.setup()
    const session = {
      id: 'session-1', status: 'drafting', revision: 1, current_stage: 'concepts',
      runs: [{
        id: 'run-1', session_id: 'session-1', stage: 'concepts', status: 'running',
        operation_id: 'operation-1', model_source: 'openai:test', attempt: 1,
        current_message: '正在生成一套创意方向',
      }],
      draft: {
        form: { brief: '记忆病毒', preset_id: 'suspense', genre: '悬疑推理', target_audience: '成年大众', platform: '暂不确定', target_words: 600000, target_chapters: 240, world_tone: '信息公平', story_structure: '三层谜团', pacing: '证据推进', writing_style: '精确克制', special_requirements: [], avoid: [] },
        concepts: [], stages: {},
      },
    }
    const closeSource = vi.fn()
    const eventSource = vi.fn().mockImplementation(function EventSourceStub() {
      return { addEventListener: vi.fn(), close: closeSource, onerror: null }
    })
    vi.stubGlobal('EventSource', eventSource)
    mockGet.mockImplementation((url: string) => {
      if (url === '/novel-creation/presets') return Promise.resolve({ data: { data: presets } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [session] } } })
      if (url === '/novel-creation/sessions/session-1') return Promise.resolve({ data: { data: session } })
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    mockPost.mockResolvedValue({ data: { data: { status: 'cancelling' } } })

    const view = renderPage('/novel-creation?session=session-1&run=run-1&model=openai%3Atest')

    await waitFor(() => {
      expect(eventSource).toHaveBeenCalledWith('/api/v1/novel-creation/runs/run-1/stream')
    })
    expect(screen.getByText('正在生成一套创意方向')).toBeInTheDocument()
    expect(screen.getByText('实际模型：openai:test')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '新建立项' })).toBeDisabled()
    const pauseButton = screen.getByRole('button', { name: /暂停/ })
    await user.click(pauseButton)
    expect(mockPost).toHaveBeenCalledWith('/operations/operation-1/pause')
    const resumeButton = await screen.findByRole('button', { name: /继续任务/ })
    await user.click(resumeButton)
    expect(mockPost).toHaveBeenCalledWith('/operations/operation-1/continue')
    const cancelButton = screen.getByRole('button', { name: /取消任务/ })
    expect(cancelButton).toBeEnabled()
    await user.click(cancelButton)
    expect(mockPost).toHaveBeenCalledWith('/operations/operation-1/cancel')
    view.unmount()
    expect(closeSource).toHaveBeenCalledTimes(1)
  })

  it('shows live model output progress without inventing a completion percentage', async () => {
    const session = {
      id: 'session-live', status: 'drafting', revision: 1, current_stage: 'opening_outline',
      runs: [{
        id: 'run-live', session_id: 'session-live', stage: 'opening_outline', status: 'running',
        operation_id: 'operation-live', model_source: 'deepseek:deepseek-v4-flash',
        current_message: '正在生成前3章细纲',
      }],
      draft: {
        form: { brief: '长篇悬疑', preset_id: 'suspense', genre: '悬疑推理', target_audience: '成年大众', platform: '暂不确定', target_words: 600000, target_chapters: 240, world_tone: '信息公平', story_structure: '三层谜团', pacing: '证据推进', writing_style: '精确克制', special_requirements: [], avoid: [] },
        concepts: [], stages: {},
      },
    }
    const listeners = new Map<string, (event: MessageEvent) => void>()
    vi.stubGlobal('EventSource', vi.fn().mockImplementation(function EventSourceStub() {
      return {
        addEventListener: vi.fn((name: string, listener: (event: MessageEvent) => void) => listeners.set(name, listener)),
        close: vi.fn(),
        onerror: null,
      }
    }))
    mockGet.mockImplementation((url: string) => {
      if (url === '/novel-creation/presets') return Promise.resolve({ data: { data: presets } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [session] } } })
      if (url === '/novel-creation/sessions/session-live') return Promise.resolve({ data: { data: session } })
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })

    renderPage('/novel-creation?session=session-live&run=run-live')
    await waitFor(() => expect(listeners.has('model_output')).toBe(true))

    act(() => listeners.get('model_output')?.(new MessageEvent('model_output', {
      data: JSON.stringify({
        event_type: 'model_output',
        message: '模型正在生成并校验立项内容 · 已输出 12,345 字',
        payload: {
          kind: 'model_output',
          output_chars: 12345,
          output_preview: '第三章的结尾钩子正在形成',
          max_output_tokens: 300000,
          attempt: 1,
        },
      }),
    })))

    expect(await screen.findByText('模型正在生成并校验立项内容 · 已输出 12,345 字')).toBeInTheDocument()
    expect(screen.getByText('已输出：12,345 字')).toBeInTheDocument()
    expect(screen.getByText('第三章的结尾钩子正在形成')).toBeInTheDocument()
    expect(screen.queryByText(/\d+%/)).not.toBeInTheDocument()
  })

  it('finalizes a run from REST when the SSE connection closes after completion', async () => {
    const persistedSessionRun = {
      id: 'run-1', session_id: 'session-1', stage: 'concepts', status: 'running',
      operation_id: 'operation-1', current_message: '正在生成一套创意方向',
    }
    let currentRun = persistedSessionRun
    const session = {
      id: 'session-1', status: 'drafting', revision: 1, current_stage: 'concepts',
      runs: [persistedSessionRun],
      draft: {
        form: { brief: '记忆病毒', preset_id: 'suspense', genre: '悬疑推理', target_audience: '成年大众', platform: '暂不确定', target_words: 600000, target_chapters: 240, world_tone: '信息公平', story_structure: '三层谜团', pacing: '证据推进', writing_style: '精确克制', special_requirements: [], avoid: [] },
        concepts: [], stages: {},
      },
    }
    let sourceInstance: {
      onerror: ((event: Event) => void) | null
      onopen: null
      close: ReturnType<typeof vi.fn>
      addEventListener: ReturnType<typeof vi.fn>
    } | undefined
    vi.stubGlobal('EventSource', vi.fn().mockImplementation(function EventSourceStub() {
      sourceInstance = { addEventListener: vi.fn(), close: vi.fn(), onerror: null, onopen: null }
      return sourceInstance
    }))
    mockGet.mockImplementation((url: string) => {
      if (url === '/novel-creation/presets') return Promise.resolve({ data: { data: presets } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [session] } } })
      if (url === '/novel-creation/sessions/session-1') return Promise.resolve({ data: { data: { ...session, runs: [{ ...persistedSessionRun }] } } })
      if (url === '/novel-creation/runs/run-1') {
        currentRun = { ...currentRun, status: 'completed', current_message: '创意方向已保存' }
        return Promise.resolve({ data: { data: currentRun } })
      }
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })

    renderPage('/novel-creation?session=session-1&run=run-1')
    await waitFor(() => expect(sourceInstance?.onerror).toBeTypeOf('function'))
    act(() => sourceInstance?.onerror?.(new Event('error')))

    expect(await screen.findByText('本轮立项任务已完成')).toBeInTheDocument()
    expect(screen.getByText('创意方向已保存')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /取消任务/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '新建立项' })).toBeEnabled()
    expect(sourceInstance?.close).toHaveBeenCalled()
  })

  it('restores a requested terminal run and keeps its outcome visible after refresh', async () => {
    const completedRun = {
      id: 'run-completed', session_id: 'session-1', stage: 'world_style', status: 'completed',
      model_source: 'openai:gpt-test', attempt: 2, result_mode: 'repaired',
      current_message: '文风与世界观已生成', warning: '模型结构已自动修复',
      next_action: '审阅并确认文风与世界观',
    }
    const session = {
      id: 'session-1', status: 'reviewing', revision: 3, current_stage: 'world_style',
      runs: [
        completedRun,
        { id: 'run-newer', session_id: 'session-1', stage: 'characters', status: 'failed' },
      ],
      draft: {
        form: { brief: '记忆病毒', preset_id: 'suspense', genre: '悬疑推理', target_audience: '成年大众', platform: '暂不确定', target_words: 600000, target_chapters: 240, world_tone: '信息公平', story_structure: '三层谜团', pacing: '证据推进', writing_style: '精确克制', special_requirements: [], avoid: [] },
        concepts: [], stages: {},
      },
    }
    mockGet.mockImplementation((url: string) => {
      if (url === '/novel-creation/presets') return Promise.resolve({ data: { data: presets } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [session] } } })
      if (url === '/novel-creation/sessions/session-1') return Promise.resolve({ data: { data: session } })
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })

    renderPage('/novel-creation?session=session-1&run=run-completed')

    expect(await screen.findByText('本轮立项任务已完成')).toBeInTheDocument()
    expect(screen.getByText('文风与世界观已生成')).toBeInTheDocument()
    expect(screen.getByText('openai:gpt-test')).toBeInTheDocument()
    expect(screen.getByText('2 次')).toBeInTheDocument()
    expect((await screen.findAllByText('阶段结果已保存到立项草稿')).length).toBeGreaterThan(0)
    expect(screen.getByText('模型结构已自动修复')).toBeInTheDocument()
    expect(screen.getByText('审阅并确认文风与世界观')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /取消任务/ })).not.toBeInTheDocument()
  })

  it('offers author-led, exploration, and existing-novel import entry points', async () => {
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByRole('button', { name: /按我的设定立项/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /帮我探索创意/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /导入已有小说/ }))
    expect(mockNavigate).toHaveBeenCalledWith('/dashboard?create=import')

    await user.click(screen.getByRole('button', { name: /按我的设定立项/ }))
    expect(await screen.findByLabelText('已有故事方案')).toBeInTheDocument()
    expect(screen.getByLabelText(/已有大纲/)).toBeInTheDocument()
    expect(screen.getByText('不可改动的设定')).toBeInTheDocument()
  })

  it('refreshes the workbench after every completed quick-generation stage', async () => {
    const session = {
      id: 'session-1', status: 'reviewing', revision: 2, current_stage: 'world_style',
      runs: [{ id: 'run-all', stage: 'all', status: 'running', current_message: '正在生成完整立项档案' }],
      draft: {
        form: { brief: '记忆病毒', preset_id: 'suspense', genre: '悬疑推理', target_audience: '成年大众', platform: '暂不确定', target_words: 600000, target_chapters: 240, world_tone: '信息公平', story_structure: '三层谜团', pacing: '证据推进', writing_style: '精确克制', special_requirements: [], avoid: [] },
        concepts: [], stages: {},
      },
    }
    const listeners = new Map<string, (event: MessageEvent) => void>()
    vi.stubGlobal('EventSource', vi.fn().mockImplementation(function EventSourceStub() {
      return {
        addEventListener: vi.fn((name: string, listener: (event: MessageEvent) => void) => listeners.set(name, listener)),
        close: vi.fn(),
        onerror: null,
      }
    }))
    mockGet.mockImplementation((url: string) => {
      if (url === '/novel-creation/presets') return Promise.resolve({ data: { data: presets } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [session] } } })
      if (url === '/novel-creation/sessions/session-1') return Promise.resolve({ data: { data: session } })
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })

    renderPage('/novel-creation?session=session-1&run=run-all&model=openai%3Atest')
    await waitFor(() => expect(listeners.has('stage_completed')).toBe(true))
    const before = mockGet.mock.calls.filter(([url]) => url === '/novel-creation/sessions/session-1').length

    act(() => listeners.get('stage_completed')?.(new MessageEvent('stage_completed', {
      data: JSON.stringify({ event_type: 'stage_completed', payload: { stage: 'characters' } }),
    })))

    await waitFor(() => {
      const after = mockGet.mock.calls.filter(([url]) => url === '/novel-creation/sessions/session-1').length
      expect(after).toBeGreaterThan(before)
    })
  })

  it('retries the failed run stage for legacy errors instead of the visible stage', async () => {
    const session = {
      id: 'session-1', status: 'reviewing', revision: 8, current_stage: 'macro_outline',
      runs: [{ id: 'run-opening', stage: 'opening_outline', status: 'failed' }],
      last_error: {
        failure_class: 'invalid_response',
        message: '开篇细纲结构不完整',
        next_action: '草稿已保留，请重试“前3章细纲”',
        run_id: 'run-opening',
      },
      draft: {
        form: { brief: '记忆病毒', preset_id: 'suspense', genre: '悬疑推理', target_audience: '成年大众', platform: '暂不确定', target_words: 600000, target_chapters: 240, world_tone: '信息公平', story_structure: '三层谜团', pacing: '证据推进', writing_style: '精确克制', special_requirements: [], avoid: [] },
        concepts: [],
        stages: { macro_outline: { status: 'generated', data: { story_overview: '追查共同记忆', core_conflict: '保存真相会遗忘', volumes: [] } } },
      },
    }
    vi.stubGlobal('EventSource', vi.fn().mockImplementation(function EventSourceStub() {
      return { addEventListener: vi.fn(), close: vi.fn(), onerror: null }
    }))
    mockGet.mockImplementation((url: string) => {
      if (url === '/novel-creation/presets') return Promise.resolve({ data: { data: presets } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [session] } } })
      if (url === '/novel-creation/sessions/session-1') return Promise.resolve({ data: { data: session } })
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    mockPost.mockResolvedValue({ data: { data: { run: { id: 'run-opening', stage: 'opening_outline', status: 'running' } } } })

    const user = userEvent.setup()
    renderPage('/novel-creation?session=session-1&stage=macro_outline')
    await user.click(await screen.findByRole('button', { name: '重试“前3章细纲”' }))

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/novel-creation/sessions/session-1/runs', expect.objectContaining({
        stage: 'opening_outline',
        expected_revision: 8,
      }))
    })
  })

  it('keeps local form text and retries against the latest revision after a conflict', async () => {
    const initialSession = {
      id: 'session-1', status: 'drafting', revision: 2, current_stage: 'constraints',
      draft: {
        form: { brief: '服务器初稿', preset_id: 'suspense', genre: '悬疑推理', target_audience: '成年大众', platform: '暂不确定', target_words: 600000, target_chapters: 240, world_tone: '信息公平', story_structure: '三层谜团', pacing: '证据推进', writing_style: '精确克制', special_requirements: [], avoid: [] },
        concepts: [], stages: {},
      },
    }
    const latestSession = {
      ...initialSession,
      revision: 3,
      draft: { ...initialSession.draft, form: { ...initialSession.draft.form, brief: '服务器并发修改' } },
    }
    mockGet.mockImplementation((url: string) => {
      if (url === '/novel-creation/presets') return Promise.resolve({ data: { data: presets } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [initialSession] } } })
      if (url === '/novel-creation/sessions/session-1') {
        const sessionFetches = mockGet.mock.calls.filter(([path]) => path === url).length
        return Promise.resolve({ data: { data: sessionFetches > 1 ? latestSession : initialSession } })
      }
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    let patchCount = 0
    mockPatch.mockImplementation((_url: string, body: { form: typeof initialSession.draft.form }) => {
      patchCount += 1
      if (patchCount === 1) {
        const conflict = Object.assign(new Error('revision conflict'), { response: { status: 409 } })
        return Promise.reject(conflict)
      }
      return Promise.resolve({ data: { data: {
        ...latestSession,
        revision: 4,
        draft: { ...latestSession.draft, form: body.form },
      } } })
    })

    const user = userEvent.setup()
    renderPage('/novel-creation?session=session-1')
    const brief = await screen.findByRole('textbox', { name: '故事梗概或最想写的画面' })
    await user.clear(brief)
    await user.type(brief, '作者本地最新版')

    await waitFor(() => expect(mockPatch).toHaveBeenCalledTimes(2), { timeout: 4000 })
    expect(brief).toHaveValue('作者本地最新版')
    expect(mockPatch.mock.calls[0][1]).toEqual(expect.objectContaining({ expected_revision: 2 }))
    expect(mockPatch.mock.calls[1][1]).toEqual(expect.objectContaining({ expected_revision: 3 }))
  }, 30_000)

  it('edits stage fields without exposing raw JSON by default', async () => {
    const session = {
      id: 'session-1', status: 'reviewing', revision: 3, current_stage: 'world_style',
      draft: {
        form: { brief: '记忆病毒', preset_id: 'suspense', genre: '悬疑推理', target_audience: '成年大众', platform: '暂不确定', target_words: 600000, target_chapters: 240, world_tone: '信息公平', story_structure: '三层谜团', pacing: '证据推进', writing_style: '精确克制', special_requirements: [], avoid: [] },
        concepts: [{ id: 'concept-1', source_index: 0, title: '灰港遗忘症', logline: '女孩用遗忘换取感染者的记忆。', protagonist_seed: { name: '林七', identity: '医生', goal: '找母亲', lack: '害怕遗忘' }, world_hook: '记忆传播', core_conflict: '救人就会遗忘', story_engine: '读忆换线索', opening_hook: '陌生人说出她的童年', differentiators: [], risks: [], coverage: { score: 92, covered: [], missing: [] } }],
        selected_concept_id: 'concept-1',
        stages: { world_style: { status: 'generated', data: { world_tone: '信息公平', writing_style: '精确克制', story_structure: '三层谜团', pacing: '证据推进', style_rules: ['证据可回看'], worldbuilding: [{ title: '记忆传播', content: '记忆会通过接触传播' }] } } },
      },
    }
    mockGet.mockImplementation((url: string) => {
      if (url === '/novel-creation/presets') return Promise.resolve({ data: { data: presets } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [session] } } })
      if (url === '/novel-creation/sessions/session-1') return Promise.resolve({ data: { data: session } })
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    mockPatch.mockResolvedValue({ data: { data: session } })

    const user = userEvent.setup()
    renderPage('/novel-creation?session=session-1')
    expect(await screen.findByRole('button', { name: '确认当前内容' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /确认并继续/ })).toHaveAttribute('title', '确认后生成系统推荐的下一对象')
    await user.click(await screen.findByRole('button', { name: /编辑阶段内容/ }, { timeout: 3000 }))

    const toneInput = screen.getByRole('textbox', { name: '世界基调' })
    expect(toneInput).toHaveValue('信息公平')
    expect(screen.queryByRole('textbox', { name: '阶段 JSON 原文' })).not.toBeInTheDocument()
    await user.clear(toneInput)
    await user.type(toneInput, '记忆有明确代价')
    await user.click(screen.getByRole('button', { name: '保存并同步' }))

    await waitFor(() => {
      expect(mockPatch).toHaveBeenCalledWith('/novel-creation/sessions/session-1/stages/world_style', expect.objectContaining({
        data: expect.objectContaining({ world_tone: '记忆有明确代价' }),
        expected_revision: 3,
      }))
    })
  }, 30_000)

  it('allows a later pending stage while an earlier stage awaits confirmation', async () => {
    const worldData = {
      world_tone: { core_tone: '冷峻但保留希望', reader_experience: '持续感到规则压力' },
      writing_style: { narrative_perspective: '第三人称限知', sentence_rhythm: ['危机用短句', '余波用长句'] },
      story_structure: { main_line: '逃亡与揭密并进', stages: ['失控', '结盟', '反攻'] },
      pacing: { opening: '快速入局', middle: '张弛交替' },
      style_rules: ['证据可回看'],
      worldbuilding: [{ title: '记忆传播', content: '记忆会通过接触传播' }],
    }
    const session = {
      id: 'session-1', status: 'reviewing', revision: 5, current_stage: 'characters',
      stage_flow: {
        attention_stage: 'world_style',
        recommended_stage: 'world_style',
        pending_confirmations: ['world_style'],
        items: {
          world_style: { stage: 'world_style', label: '文风与世界观', status: 'generated', can_confirm: true, actions: ['view', 'edit', 'regenerate', 'confirm'], next_stage: 'characters' },
          characters: { stage: 'characters', label: '角色与关系', status: 'pending', can_confirm: false, actions: ['view', 'generate'], next_stage: 'locations' },
        },
      },
      draft: {
        form: { brief: '记忆病毒', preset_id: 'suspense', genre: '悬疑推理', target_audience: '成年大众', platform: '暂不确定', target_words: 600000, target_chapters: 240, world_tone: '信息公平', story_structure: '三层谜团', pacing: '证据推进', writing_style: '精确克制', special_requirements: [], avoid: [] },
        concepts: [{ id: 'concept-1', source_index: 0, title: '灰港遗忘症', logline: '女孩用遗忘换取感染者的记忆。', protagonist_seed: { name: '林七', identity: '医生', goal: '找母亲', lack: '害怕遗忘' }, world_hook: '记忆传播', core_conflict: '救人就会遗忘', story_engine: '读忆换线索', opening_hook: '陌生人说出她的童年', differentiators: [], risks: [], coverage: { score: 92, covered: [], missing: [] } }],
        selected_concept_id: 'concept-1',
        stages: {
          world_style: { status: 'generated', data: worldData },
          characters: { status: 'pending', data: null },
        },
      },
    }
    const confirmed = {
      ...session,
      revision: 6,
      current_stage: 'characters',
      stage_flow: {
        ...session.stage_flow,
        attention_stage: 'characters',
        recommended_stage: 'characters',
        pending_confirmations: [],
        items: {
          ...session.stage_flow.items,
          world_style: { ...session.stage_flow.items.world_style, status: 'confirmed', can_confirm: false },
          characters: { ...session.stage_flow.items.characters, actions: ['view', 'generate'] },
        },
      },
      draft: {
        ...session.draft,
        stages: {
          ...session.draft.stages,
          world_style: { status: 'confirmed', data: worldData },
        },
      },
    }
    mockGet.mockImplementation((url: string) => {
      if (url === '/novel-creation/presets') return Promise.resolve({ data: { data: presets } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [session] } } })
      if (url === '/novel-creation/sessions/session-1') return Promise.resolve({ data: { data: session } })
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    mockPost.mockResolvedValue({ data: { data: confirmed } })

    const user = userEvent.setup()
    renderPage('/novel-creation?session=session-1')

    expect(await screen.findByRole('heading', { name: '文风与世界观' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /角色与关系/ }))
    expect(await screen.findByRole('heading', { name: '角色与关系' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /生成角色与关系/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /文风与世界观.*待确认/ }))
    expect(await screen.findByRole('heading', { name: '文风与世界观' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认当前内容' }))

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/novel-creation/sessions/session-1/stages/world_style/confirm', expect.objectContaining({
        confirm: true,
        expected_revision: 5,
      }))
    })
    expect(mockPost.mock.calls.some(([url]) => String(url).endsWith('/runs'))).toBe(false)
  })
})
