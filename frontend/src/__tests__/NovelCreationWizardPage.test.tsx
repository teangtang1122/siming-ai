import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
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

  it('allows saving the intake but explains model setup when none is configured', async () => {
    modelState.hasModels = false
    renderPage()
    expect(await screen.findByText('当前没有可用模型')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /只保存草稿/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: '打开系统设置' })).toBeInTheDocument()
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
})
