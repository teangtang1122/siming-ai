import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
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
  stage_order: ['constraints', 'concepts', 'world_style'],
  stage_labels: { world_style: '文风与世界观' },
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
    await user.click(screen.getByRole('button', { name: /悬疑推理/ }))
    await user.click(screen.getByText('创作约束与高级设置'))
    expect(await screen.findByDisplayValue('信息公平')).toBeInTheDocument()
    expect(screen.getByDisplayValue('空降凶手')).toBeInTheDocument()
  })

  it('uses the only ready model directly and exposes the mobile genre-scroll hint', async () => {
    renderPage()

    expect(await screen.findByText('AI 已准备好')).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: '选择本阶段模型' })).not.toBeInTheDocument()
    expect(screen.getByText('选择题材')).toBeInTheDocument()
    expect(screen.getByText('左右滑动选择')).toBeInTheDocument()
  })

  it('allows saving the intake but explains model setup when none is configured', async () => {
    modelState.hasModels = false
    renderPage()
    expect(await screen.findByText('当前没有可用模型')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /只保存草稿/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: '免费开始' })).toBeInTheDocument()
  })

  it('restores a concept session and offers both guided and quick tracks', async () => {
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
    expect(await screen.findByText('灰港遗忘症')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '进入完整向导' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /快速生成到最终审阅/ })).toBeInTheDocument()
  })

  it('reconnects to the active lightweight-concept run after a handoff', async () => {
    const session = {
      id: 'session-1', status: 'drafting', revision: 1, current_stage: 'concepts',
      runs: [{ id: 'run-1', stage: 'concepts', status: 'running', current_message: '正在生成三套轻量创意' }],
      draft: {
        form: { brief: '记忆病毒', preset_id: 'suspense', genre: '悬疑推理', target_audience: '成年大众', platform: '暂不确定', target_words: 600000, target_chapters: 240, world_tone: '信息公平', story_structure: '三层谜团', pacing: '证据推进', writing_style: '精确克制', special_requirements: [], avoid: [] },
        concepts: [], stages: {},
      },
    }
    const eventSource = vi.fn().mockImplementation(function EventSourceStub() {
      return { addEventListener: vi.fn(), close: vi.fn(), onerror: null }
    })
    vi.stubGlobal('EventSource', eventSource)
    mockGet.mockImplementation((url: string) => {
      if (url === '/novel-creation/presets') return Promise.resolve({ data: { data: presets } })
      if (url === '/novel-creation/sessions') return Promise.resolve({ data: { data: { sessions: [session] } } })
      if (url === '/novel-creation/sessions/session-1') return Promise.resolve({ data: { data: session } })
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })

    renderPage('/novel-creation?session=session-1&run=run-1&model=openai%3Atest')

    await waitFor(() => {
      expect(eventSource).toHaveBeenCalledWith('/api/v1/novel-creation/runs/run-1/stream')
    })
    expect(screen.getByText('正在生成三套轻量创意')).toBeInTheDocument()
  })

  it('starts only the first stage when the author chooses the guided track', async () => {
    const session = {
      id: 'session-1', status: 'reviewing', revision: 2, current_stage: 'world_style',
      draft: {
        form: { brief: '记忆病毒', preset_id: 'suspense', genre: '悬疑推理', target_audience: '成年大众', platform: '暂不确定', target_words: 600000, target_chapters: 240, world_tone: '信息公平', story_structure: '三层谜团', pacing: '证据推进', writing_style: '精确克制', special_requirements: [], avoid: [] },
        concepts: [{ id: 'concept-1', source_index: 0, title: '灰港遗忘症', logline: '女孩用遗忘换取感染者的记忆。', protagonist_seed: { name: '林七', identity: '医生', goal: '找母亲', lack: '害怕遗忘' }, world_hook: '记忆传播', core_conflict: '救人就会遗忘', story_engine: '读忆换线索', opening_hook: '陌生人说出她的童年', differentiators: ['记忆感染'], risks: ['规则需稳定'], coverage: { score: 92, covered: [], missing: [] } }],
        stages: {},
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
    mockPatch.mockResolvedValue({ data: { data: session } })
    mockPost.mockImplementation((url: string) => {
      if (url.endsWith('/runs')) return Promise.resolve({ data: { data: { run: { id: 'run-world', stage: 'world_style', status: 'running' } } } })
      return Promise.resolve({ data: { data: session } })
    })

    const user = userEvent.setup()
    renderPage('/novel-creation?session=session-1')
    await user.click(await screen.findByRole('button', { name: '进入完整向导' }))

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/novel-creation/sessions/session-1/runs', expect.objectContaining({
        stage: 'world_style',
        auto_confirm: false,
        expected_revision: 2,
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
  }, 10_000)

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
    mockPost.mockResolvedValue({ data: { data: session } })

    const user = userEvent.setup()
    renderPage('/novel-creation?session=session-1')
    await user.click(await screen.findByRole('button', { name: /编辑阶段内容/ }, { timeout: 3000 }))

    const toneInput = screen.getByRole('textbox', { name: '世界基调' })
    expect(toneInput).toHaveValue('信息公平')
    expect(screen.queryByRole('textbox', { name: '阶段 JSON 原文' })).not.toBeInTheDocument()
    await user.clear(toneInput)
    await user.type(toneInput, '记忆有明确代价')
    await user.click(screen.getByRole('button', { name: '保存修改' }))

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/novel-creation/sessions/session-1/stages/world_style/confirm', expect.objectContaining({
        data: expect.objectContaining({ world_tone: '记忆有明确代价' }),
        confirm: false,
      }))
    })
  }, 10_000)
})
