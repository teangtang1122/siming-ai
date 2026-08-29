import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('../api/client', () => ({ apiClient: api }))
vi.mock('../hooks/useModelOptions', () => ({
  useModelOptions: () => ({
    modelOptions: [{
      value: 'opencode_cli:test-model',
      label: 'OpenCode CLI · test-model（全局默认）',
      provider: 'opencode_cli',
      model: 'test-model',
      isGlobalDefault: true,
    }],
    defaultModel: 'opencode_cli:test-model',
    loading: false,
  }),
}))

import WriterPage from '../pages/WriterPage'
import { AiPanelProvider } from '../contexts/AiPanelContext'
import { storeNarrativeSourceLocator } from '../features/narrativeGovernance/sourceLocator'

const source = '他站在门边，心中不由得涌起一阵复杂的情绪。值得注意的是，这一切都说明命运已经改变。'
const candidate = '他扶住门框，指节压得发白。屋里那句话落下后，他半晌没有迈步。'
const qualityReport = {
  chapter_id: 'chapter-1',
  word_count: source.length,
  total_score: 56,
  max_score: 80,
  scores: [
    ['开头吸引力', 8],
    ['情节推进', 7],
    ['角色塑造', 6],
    ['对话质量', 8],
    ['悬念设置', 9],
    ['节奏控制', 7],
    ['展示性描写', 6],
    ['语言质量', 5],
  ].map(([dimension, score]) => ({ dimension, score, comment: `${dimension}评价` })),
  ai_flavor_count: 2,
  overall_assessment: '开场有效，但解释句偏多。',
  bottom3_improvements: ['语言质量：减少解释句', '角色塑造：增加动作选择', '展示性描写：补充感官细节'],
  provider: 'opencode_cli',
  model: 'test-model',
  mutated: false,
}

const chapter = {
  id: 'chapter-1',
  project_id: 'project-1',
  outline_node_id: null,
  title: '第一章',
  word_count: source.length,
  current_version: 1,
  sort_order: 1000,
  outline_title: null,
  outline_status: null,
  outline_node_type: null,
  outline_path: [],
  summary_text: null,
  key_events: [],
  created_at: '2026-08-09T12:00:00Z',
  updated_at: '2026-08-09T12:00:00Z',
  content: source,
  snapshot_count: 1,
}

const response = <T,>(data: T) => ({ data: { code: 0, message: 'ok', data } })

describe('WriterPage manual writing actions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
    api.get.mockImplementation((url: string) => {
      if (url.endsWith('/chapter-drafts/pending')) return Promise.resolve(response({ items: [], total: 0 }))
      if (url.endsWith('/outline')) return Promise.resolve(response({ items: [], flat: [], total: 0 }))
      if (url.endsWith('/chapters')) return Promise.resolve(response({ items: [chapter], total: 1 }))
      if (url.endsWith('/snapshots')) return Promise.resolve(response({ items: [], total: 0 }))
      if (url.endsWith('/chapters/chapter-1')) return Promise.resolve(response(chapter))
      throw new Error(`Unexpected GET ${url}`)
    })
    api.post.mockImplementation((url: string) => {
      if (url.endsWith('/de-ai-preview')) {
        return Promise.resolve(response({
          chapter_id: chapter.id,
          original: source,
          rewritten: candidate,
          original_word_count: source.length,
          rewritten_word_count: candidate.length,
          provider: 'opencode_cli',
          model: 'test-model',
          mutated: false,
        }))
      }
      if (url.endsWith('/quality-score-preview')) return Promise.resolve(response(qualityReport))
      throw new Error(`Unexpected POST ${url}`)
    })
    api.put.mockResolvedValue(response({
      ...chapter,
      content: candidate,
      word_count: candidate.length,
      current_version: 2,
      snapshot_count: 2,
    }))
  })

  it('reorders正文 independently from outline links', async () => {
    const secondChapter = {
      ...chapter,
      id: 'chapter-2',
      title: '第二章',
      sort_order: 2000,
      content: '第二章正文。',
    }
    api.get.mockImplementation((url: string) => {
      if (url.endsWith('/chapter-drafts/pending')) return Promise.resolve(response({ items: [], total: 0 }))
      if (url.endsWith('/outline')) return Promise.resolve(response({ items: [], flat: [], total: 0 }))
      if (url.endsWith('/chapters')) return Promise.resolve(response({ items: [chapter, secondChapter], total: 2 }))
      if (url.endsWith('/snapshots')) return Promise.resolve(response({ items: [], total: 0 }))
      if (url.endsWith('/chapters/chapter-1')) return Promise.resolve(response(chapter))
      throw new Error(`Unexpected GET ${url}`)
    })
    api.put.mockImplementation((url: string, payload: { ids?: string[] }) => {
      if (url.endsWith('/chapters/reorder')) {
        expect(payload.ids).toEqual(['chapter-2', 'chapter-1'])
        return Promise.resolve(response({
          items: [
            { ...secondChapter, sort_order: 1000 },
            { ...chapter, sort_order: 2000 },
          ],
          total: 2,
        }))
      }
      throw new Error(`Unexpected PUT ${url}`)
    })

    render(<WriterPage projectId="project-1" />)
    const up = await screen.findByRole('button', { name: '上移章节：第二章' })
    fireEvent.click(up)

    await waitFor(() => expect(api.put).toHaveBeenCalledWith(
      '/projects/project-1/chapters/reorder',
      { ids: ['chapter-2', 'chapter-1'] },
    ))
    expect(await screen.findByText('正文顺序已更新')).toBeInTheDocument()
  })

  it('keeps even a legacy targeted draft separate and saves it as a new chapter', async () => {
    const secondDraft = {
      draft_id: 'draft-2',
      project_id: 'project-1',
      title: '第2章 夜雨',
      outline_node_id: 'outline-2',
      context_manifest_id: 'manifest-2',
      target_chapter_id: 'chapter-1',
      saved_chapter_id: null,
      draft_status: 'pending' as const,
      content: '夜雨落在山门外，第二章由此开始。',
      word_count: 16,
    }
    api.get.mockImplementation((url: string) => {
      if (url.endsWith('/chapter-drafts/pending')) return Promise.resolve(response(secondDraft))
      if (url.endsWith('/outline')) return Promise.resolve(response({ items: [], flat: [], total: 0 }))
      if (url.endsWith('/chapters')) return Promise.resolve(response({ items: [chapter], total: 1 }))
      if (url.endsWith('/snapshots')) return Promise.resolve(response({ items: [], total: 0 }))
      if (url.endsWith('/chapters/chapter-1')) return Promise.resolve(response(chapter))
      throw new Error(`Unexpected GET ${url}`)
    })
    api.post.mockImplementation((url: string) => {
      if (url.endsWith('/chapters')) {
        return Promise.resolve(response({
          ...chapter,
          id: 'chapter-2',
          title: secondDraft.title,
          outline_node_id: secondDraft.outline_node_id,
          content: secondDraft.content,
          word_count: secondDraft.word_count,
          cataloging_required: true,
          cataloging_job: { started: true, status: 'running' },
        }))
      }
      throw new Error(`Unexpected POST ${url}`)
    })

    render(
      <AiPanelProvider>
        <WriterPage projectId="project-1" />
      </AiPanelProvider>,
    )

    expect(await screen.findByLabelText('当前草稿：第2章 夜雨')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '打开章节：第一章' })).toBeInTheDocument()
    expect(await screen.findByText('第2章 夜雨 · 未保存')).toBeInTheDocument()
    await waitFor(() => {
      expect(document.querySelector<HTMLTextAreaElement>('.writer-content-input textarea'))
        .toHaveValue(secondDraft.content)
    })
    fireEvent.click(screen.getByRole('button', { name: '打开章节：第一章' }))
    expect(await screen.findByText('请先保存当前 AI 章节草稿；保存前草稿是正文编辑器的唯一内容'))
      .toBeInTheDocument()
    expect(document.querySelector<HTMLTextAreaElement>('.writer-content-input textarea'))
      .toHaveValue(secondDraft.content)
    fireEvent.click(screen.getByRole('button', { name: '保存并建档' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/projects/project-1/chapters',
      expect.objectContaining({ draft_id: 'draft-2', content: secondDraft.content }),
    ))
    expect(api.put).not.toHaveBeenCalled()
  })

  it('keeps a failed pending-draft save visible in the editor', async () => {
    const pendingDraft = {
      draft_id: 'draft-save-error',
      project_id: 'project-1',
      title: '第二章 迟到草稿',
      outline_node_id: 'outline-2',
      context_manifest_id: 'manifest-2',
      saved_chapter_id: null,
      draft_status: 'pending' as const,
      content: '这份草稿的保存错误必须明确显示。',
      word_count: 16,
    }
    api.get.mockImplementation((url: string) => {
      if (url.endsWith('/chapter-drafts/pending')) return Promise.resolve(response(pendingDraft))
      if (url.endsWith('/outline')) return Promise.resolve(response({ items: [], flat: [], total: 0 }))
      if (url.endsWith('/chapters')) return Promise.resolve(response({ items: [chapter], total: 1 }))
      if (url.endsWith('/snapshots')) return Promise.resolve(response({ items: [], total: 0 }))
      if (url.endsWith('/chapters/chapter-1')) return Promise.resolve(response(chapter))
      throw new Error(`Unexpected GET ${url}`)
    })
    const detail = '迟到草稿已释放，不会阻塞后续写作'
    api.post.mockRejectedValue(Object.assign(new Error('保存失败'), {
      response: { data: { detail } },
    }))

    render(
      <AiPanelProvider>
        <WriterPage projectId="project-1" />
      </AiPanelProvider>,
    )

    await screen.findByLabelText('当前草稿：第二章 迟到草稿')
    fireEvent.click(screen.getByRole('button', { name: '保存并建档' }))

    expect(await screen.findByText('保存失败', {}, { timeout: 5000 })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getAllByText(detail).length).toBeGreaterThan(0)
    }, { timeout: 5000 })
    expect(await screen.findByLabelText('当前草稿：第二章 迟到草稿')).toBeInTheDocument()
  })

  it('ignores a saved chapter detail response that arrives after the draft', async () => {
    const lateDraft = {
      draft_id: 'draft-late',
      project_id: 'project-1',
      title: '第二章 夜雨',
      outline_node_id: 'outline-2',
      context_manifest_id: 'manifest-2',
      saved_chapter_id: null,
      draft_status: 'pending' as const,
      content: '第二章草稿必须一直占有编辑器。',
      word_count: 15,
    }
    let resolvePendingDraft!: (value: ReturnType<typeof response<typeof lateDraft>>) => void
    let resolveChapterDetail!: (value: ReturnType<typeof response<typeof chapter>>) => void
    const pendingDraftRequest = new Promise<ReturnType<typeof response<typeof lateDraft>>>((resolve) => {
      resolvePendingDraft = resolve
    })
    const chapterDetailRequest = new Promise<ReturnType<typeof response<typeof chapter>>>((resolve) => {
      resolveChapterDetail = resolve
    })
    api.get.mockImplementation((url: string) => {
      if (url.endsWith('/chapter-drafts/pending')) return pendingDraftRequest
      if (url.endsWith('/outline')) return Promise.resolve(response({ items: [], flat: [], total: 0 }))
      if (url.endsWith('/chapters')) return Promise.resolve(response({ items: [chapter], total: 1 }))
      if (url.endsWith('/chapters/chapter-1')) return chapterDetailRequest
      if (url.endsWith('/snapshots')) return Promise.resolve(response({ items: [], total: 0 }))
      throw new Error(`Unexpected GET ${url}`)
    })

    render(
      <AiPanelProvider>
        <WriterPage projectId="project-1" />
      </AiPanelProvider>,
    )

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/projects/project-1/chapters/chapter-1'))
    await act(async () => { resolvePendingDraft(response(lateDraft)) })
    await waitFor(() => {
      expect(document.querySelector<HTMLTextAreaElement>('.writer-content-input textarea'))
        .toHaveValue(lateDraft.content)
    })
    await act(async () => { resolveChapterDetail(response(chapter)) })

    await waitFor(() => {
      expect(document.querySelector<HTMLTextAreaElement>('.writer-content-input textarea'))
        .toHaveValue(lateDraft.content)
    })
    expect(screen.getByLabelText('当前草稿：第二章 夜雨')).toBeInTheDocument()
    expect(screen.queryByDisplayValue(source)).not.toBeInTheDocument()
  })

  it('previews without writing, then saves an explicitly applied candidate as de_ai', async () => {
    render(<WriterPage projectId="project-1" />)

    const reviseButton = await screen.findByRole('button', { name: /去除 AI 味/ })
    await waitFor(() => expect(reviseButton).toBeEnabled())
    fireEvent.click(reviseButton)

    expect(await screen.findByText('这是一项独立修订，任何审核结果都不会自动覆盖正文')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '生成候选稿' }))

    const candidateDiff = await screen.findByLabelText('候选稿差异，绿色表示新增或改写后的内容')
    expect(candidateDiff).toHaveTextContent(candidate)
    expect(screen.getByLabelText('差异颜色说明')).toBeInTheDocument()
    expect(document.querySelector('.writer-de-ai-diff-removed')).toBeInTheDocument()
    expect(document.querySelector('.writer-de-ai-diff-added')).toBeInTheDocument()
    expect(api.post).toHaveBeenCalledWith(
      '/projects/project-1/chapters/chapter-1/de-ai-preview',
      { content: source, model: 'opencode_cli:test-model' },
    )
    expect(api.put).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '替换到编辑器' }))
    expect(await screen.findByText('去除 AI 味候选稿已应用，尚未保存')).toBeInTheDocument()
    await waitFor(() => {
      expect(document.querySelector<HTMLTextAreaElement>('.writer-content-input textarea')).toHaveValue(candidate)
    })
    expect(api.put).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '保存并建档' }))
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(api.put.mock.calls[0][1]).toMatchObject({
      content: candidate,
      cataloging_mode: 'save_and_catalog',
      trigger_type: 'de_ai',
    })
  })

  it('keeps an audit-rejected candidate visible beside the unchanged original', async () => {
    const warning = {
      source: 'fidelity_audit',
      code: 'contradiction',
      detail: '条件关系仍需作者确认',
      chunk: 1,
    }
    api.post.mockImplementation((url: string) => {
      if (url.endsWith('/de-ai-preview')) {
        return Promise.resolve(response({
          chapter_id: chapter.id,
          original: source,
          rewritten: candidate,
          original_word_count: source.length,
          rewritten_word_count: candidate.length,
          provider: 'codex_cli',
          model: 'test-model',
          mutated: false,
          persisted: false,
          auto_adopted: false,
          review_required: true,
          audit_passed: false,
          candidate_status: 'review_with_warnings',
          warnings: [warning],
        }))
      }
      if (url.endsWith('/quality-score-preview')) return Promise.resolve(response(qualityReport))
      throw new Error(`Unexpected POST ${url}`)
    })

    render(<WriterPage projectId="project-1" />)
    const reviseButton = await screen.findByRole('button', { name: /去除 AI 味/ })
    await waitFor(() => expect(reviseButton).toBeEnabled())
    fireEvent.click(reviseButton)
    fireEvent.click(screen.getByRole('button', { name: '生成候选稿' }))

    expect(await screen.findByLabelText('候选稿差异，绿色表示新增或改写后的内容'))
      .toHaveTextContent(candidate)
    expect(screen.getByText('原文（未变更）')).toBeInTheDocument()
    expect(screen.getByText('候选稿（第 1 轮，未采用）')).toBeInTheDocument()
    expect(screen.getByText('候选稿有 1 项系统审核提醒，但仍完整保留供你查看')).toBeInTheDocument()
    expect(screen.getByText('第 1 段：条件关系仍需作者确认')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '仍要替换到编辑器' })).toBeInTheDocument()
    expect(document.querySelector<HTMLTextAreaElement>('.writer-content-input textarea')).toHaveValue(source)
    expect(api.put).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '仍要替换到编辑器' }))
    expect((await screen.findAllByText('候选稿有审核提醒，仍要替换吗？')).length).toBeGreaterThan(0)
    expect(document.querySelector<HTMLTextAreaElement>('.writer-content-input textarea')).toHaveValue(source)
    const confirmButtons = screen.getAllByRole('button', { name: '仍要替换到编辑器' })
    fireEvent.click(confirmButtons[confirmButtons.length - 1])
    await waitFor(() => {
      expect(document.querySelector<HTMLTextAreaElement>('.writer-content-input textarea')).toHaveValue(candidate)
    })
    expect(api.put).not.toHaveBeenCalled()
  })

  it('continues from the previous system candidate for at most three review rounds', async () => {
    const round2 = '门框硌着他的掌心。屋里的话已经说完，他却仍停在门口。'
    const round3 = '掌心抵着门框。他听完屋里那句话，脚还留在门外。'
    let revisionRound = 0
    api.post.mockImplementation((url: string, payload: Record<string, unknown>) => {
      if (url.endsWith('/de-ai-preview')) {
        revisionRound += 1
        const rewritten = revisionRound === 1 ? candidate : revisionRound === 2 ? round2 : round3
        return Promise.resolve(response({
          chapter_id: chapter.id,
          original: source,
          input: payload.content,
          rewritten,
          original_word_count: source.length,
          input_word_count: String(payload.content || '').length,
          rewritten_word_count: rewritten.length,
          provider: 'opencode_cli',
          model: 'test-model',
          mutated: false,
          persisted: false,
          auto_adopted: false,
          review_required: true,
          audit_passed: true,
          revision_round: revisionRound,
          max_revision_rounds: 3,
          can_continue: revisionRound < 3,
          warnings: [],
        }))
      }
      if (url.endsWith('/quality-score-preview')) return Promise.resolve(response(qualityReport))
      throw new Error(`Unexpected POST ${url}`)
    })

    render(<WriterPage projectId="project-1" />)
    const reviseButton = await screen.findByRole('button', { name: /去除 AI 味/ })
    await waitFor(() => expect(reviseButton).toBeEnabled())
    fireEvent.click(reviseButton)
    fireEvent.click(screen.getByRole('button', { name: '生成候选稿' }))

    expect(await screen.findByLabelText('候选稿差异，绿色表示新增或改写后的内容'))
      .toHaveTextContent(candidate)
    const round2Button = screen.getByRole('button', { name: '继续处理候选稿（第 2/3 轮）' })
    await waitFor(() => expect(round2Button).toBeEnabled())
    fireEvent.click(round2Button)
    await waitFor(() => {
      expect(screen.getByLabelText('候选稿差异，绿色表示新增或改写后的内容'))
        .toHaveTextContent(round2)
    })
    expect(api.post.mock.calls[1][1]).toEqual({
      content: candidate,
      original_content: source,
      revision_round: 2,
      model: 'opencode_cli:test-model',
    })

    await screen.findByText('第 2/3 轮')
    const round3Button = await screen.findByRole('button', { name: /继续处理候选稿.*第 3\/3 轮/ })
    await waitFor(() => expect(round3Button).toBeEnabled())
    fireEvent.click(round3Button)
    await waitFor(() => {
      expect(screen.getByLabelText('候选稿差异，绿色表示新增或改写后的内容'))
        .toHaveTextContent(round3)
    })
    expect(api.post.mock.calls[2][1]).toEqual({
      content: round2,
      original_content: source,
      revision_round: 3,
      model: 'opencode_cli:test-model',
    })
    expect(screen.queryByRole('button', { name: /继续处理候选稿/ })).not.toBeInTheDocument()
    expect(document.querySelector('.writer-de-ai-pane:not(.writer-de-ai-pane-candidate) pre')).toHaveTextContent(source)
    expect(document.querySelector<HTMLTextAreaElement>('.writer-content-input textarea')).toHaveValue(source)
    expect(api.put).not.toHaveBeenCalled()
  })

  it('scores only after an explicit click and never rewrites the chapter', async () => {
    render(<WriterPage projectId="project-1" />)

    const scoreButton = await screen.findByRole('button', { name: /质量评分/ })
    await waitFor(() => expect(scoreButton).toBeEnabled())
    fireEvent.click(scoreButton)

    expect(await screen.findByText('手动评分只做检查，不会改写或保存正文')).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '开始评分' }))

    expect(await screen.findByLabelText('质量总分 56 分，共 80 分')).toBeInTheDocument()
    expect(screen.getByText('开场有效，但解释句偏多。')).toBeInTheDocument()
    expect(screen.getByText('AI 味线索 2 处')).toBeInTheDocument()
    expect(api.post).toHaveBeenCalledWith(
      '/projects/project-1/chapters/chapter-1/quality-score-preview',
      { title: '第一章', content: source, model: 'opencode_cli:test-model' },
    )
    expect(api.put).not.toHaveBeenCalled()
  })

  it('opens the chapter requested by narrative governance', async () => {
    const focusedChapter = {
      ...chapter,
      id: 'chapter-2',
      title: '第二章 血纹来源',
      content: '铸剑师终于说明血纹来自旧祭坛。',
    }
    api.get.mockImplementation((url: string) => {
      if (url.endsWith('/chapter-drafts/pending')) return Promise.resolve(response({ items: [], total: 0 }))
      if (url.endsWith('/outline')) return Promise.resolve(response({ items: [], flat: [], total: 0 }))
      if (url.endsWith('/chapters')) return Promise.resolve(response({ items: [chapter, focusedChapter], total: 2 }))
      if (url.endsWith('/chapter-1/snapshots') || url.endsWith('/chapter-2/snapshots')) return Promise.resolve(response({ items: [], total: 0 }))
      if (url.endsWith('/chapters/chapter-1')) return Promise.resolve(response(chapter))
      if (url.endsWith('/chapters/chapter-2')) return Promise.resolve(response(focusedChapter))
      throw new Error(`Unexpected GET ${url}`)
    })

    render(<WriterPage projectId="project-1" focusChapterId="chapter-2" />)

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/projects/project-1/chapters/chapter-2'))
    expect(await screen.findByDisplayValue('第二章 血纹来源')).toBeInTheDocument()
  })

  it('selects and focuses the exact evidence requested by narrative governance', async () => {
    const evidence = '不由得涌起一阵复杂的情绪'
    const locatorKey = storeNarrativeSourceLocator({
      projectId: 'project-1',
      chapterId: 'chapter-1',
      evidence: `原文：“${evidence}”`,
      governanceItemId: 'governance-1',
      sourceVersion: 1,
    })
    expect(locatorKey).toBeTruthy()

    render(
      <WriterPage
        projectId="project-1"
        focusChapterId="chapter-1"
        sourceLocatorKey={locatorKey || undefined}
      />,
    )

    const editor = await screen.findByDisplayValue(source) as HTMLTextAreaElement
    const expectedStart = source.indexOf(evidence)
    await waitFor(() => {
      expect(editor.selectionStart).toBe(expectedStart)
      expect(editor.selectionEnd).toBe(expectedStart + evidence.length)
    })
    expect(document.activeElement).toBe(editor)
  })
})
