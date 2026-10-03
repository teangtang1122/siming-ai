import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { AiPanelProvider } from '../contexts/AiPanelContext'

const api = vi.hoisted(() => ({
  delete: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}))
vi.mock('../api/client', () => ({ apiClient: api }))
vi.mock('../hooks/useUnsavedGuard', async () => {
  const React = await vi.importActual<typeof import('react')>('react')
  return {
    useUnsavedGuard: () => {
      const [saveStatus, setSaveStatus] = React.useState<'saved' | 'dirty' | 'saving' | 'error'>('saved')
      const [saveError, setSaveError] = React.useState<string | null>(null)
      return {
        saveStatus,
        saveError,
        confirmLeave: (action: () => void) => action(),
        markDirty: () => {
          setSaveStatus('dirty')
          setSaveError(null)
        },
        markSaved: () => {
          setSaveStatus('saved')
          setSaveError(null)
        },
        markSaving: () => {
          setSaveStatus('saving')
          setSaveError(null)
        },
        markSaveFailed: (error?: string) => {
          setSaveStatus('error')
          setSaveError(error || '保存失败，请重试。')
        },
      }
    },
  }
})

import OutlinePage from '../pages/OutlinePage'

function renderPage() {
  return render(<MemoryRouter><AiPanelProvider><OutlinePage projectId="project-1" /></AiPanelProvider></MemoryRouter>)
}

const formalVolume = {
  id: 'volume-1', project_id: 'project-1', parent_id: null, node_type: 'volume',
  title: '第一卷', summary: '原有卷纲', status: 'pending', sort_order: 0,
  metadata: { start_chapter: 1, end_chapter: 40 }, linked_characters: [], children: [],
}
const pendingDraft = {
  draft_id: 'draft-1', project_id: 'project-1', parent_id: 'volume-1',
  draft_status: 'pending', design_notes: '设计说明',
  nodes: [{ node_type: 'chapter', title: '第四章', summary: '待确认剧情', status: 'pending' }],
}

function mockExistingOutline(draft: typeof pendingDraft | null = null, formal = formalVolume) {
  api.get.mockImplementation((url: string) => {
    if (url.endsWith('/outline')) return Promise.resolve({ data: { data: { items: [formal], flat: [formal], total: 1 } } })
    if (url.endsWith('/outline-drafts/pending')) return Promise.resolve({ data: { data: draft } })
    if (url.endsWith('/characters')) return Promise.resolve({ data: { data: { items: characters, total: characters.length } } })
    throw new Error(`Unexpected GET ${url}`)
  })
}

const characters = Array.from({ length: 4 }, (_, index) => ({
  id: `character-${index + 1}`,
  name: `角色${index + 1}`,
  role_type: 'supporting',
}))

function mockInitialRequests() {
  api.get.mockImplementation((url: string) => {
    if (url === '/projects/project-1/outline') {
      return Promise.resolve({
        data: { data: { items: [], flat: [], total: 0 } },
      })
    }
    if (url === '/projects/project-1/characters') {
      return Promise.resolve({
        data: { data: { items: characters, total: characters.length } },
      })
    }
    if (url === '/projects/project-1/outline-drafts/pending') {
      return Promise.resolve({ data: { data: null } })
    }
    throw new Error(`Unexpected GET ${url}`)
  })
}

async function selectAllCharacters() {
  const selector = screen.getByLabelText('关联角色')
  for (const character of characters) {
    fireEvent.mouseDown(selector)
    fireEvent.click(await screen.findByText(`${character.name} · ${character.role_type}`))
  }
}

describe('OutlinePage', () => {
  beforeEach(() => {
    api.delete.mockReset()
    api.get.mockReset()
    api.post.mockReset()
    api.put.mockReset()
    mockInitialRequests()
  })

  it('keeps a failed new outline visible, then retries with all four character ids', async () => {
    api.post.mockRejectedValue(new Error('大纲关联保存失败'))
    renderPage()

    const createButton = (await screen.findByText('新增节点')).closest('button')
    expect(createButton).not.toBeNull()
    fireEvent.click(createButton!)
    await screen.findByRole('heading', { name: '新建大纲节点' })
    fireEvent.change(screen.getByLabelText('标题'), { target: { value: '四角色节点' } })
    await selectAllCharacters()
    fireEvent.click(screen.getByRole('button', { name: /保存/ }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/projects/project-1/outline',
      expect.objectContaining({
        title: '四角色节点',
        character_ids: characters.map((character) => character.id),
      }),
    ))
    expect((await screen.findAllByText('大纲关联保存失败')).length).toBeGreaterThan(0)
    expect(screen.getByText('保存失败')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '新建大纲节点' })).toBeInTheDocument()
    expect(screen.getByLabelText('标题')).toHaveValue('四角色节点')
    await waitFor(() => expect(screen.getByRole('button', { name: /保存/ })).not.toBeDisabled())

    api.post.mockResolvedValueOnce({
      data: {
        data: {
          id: 'outline-1',
          project_id: 'project-1',
          parent_id: null,
          node_type: 'chapter',
          title: '四角色节点',
          summary: null,
          status: 'pending',
          sort_order: 0,
          metadata: {},
          linked_characters: characters.map((character) => ({
            ...character,
            role_in_scene: null,
          })),
        },
      },
    })
    fireEvent.click(screen.getByRole('button', { name: /保存/ }))

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByText('保存失败')).not.toBeInTheDocument())
  })

  it('edits and saves the persisted draft without showing a save button for an unrelated formal volume', async () => {
    mockExistingOutline(pendingDraft)
    api.put.mockImplementation((_url: string, payload: Record<string, unknown>) => Promise.resolve({
      data: { data: { ...pendingDraft, ...payload } },
    }))
    renderPage()

    await screen.findByRole('heading', { name: 'AI 大纲草稿' })
    expect(screen.queryByRole('button', { name: /保存正式节点/ })).not.toBeInTheDocument()
    expect(screen.getByText('草稿已保存')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /保存草稿修改/ })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('草稿节点 1 标题'), { target: { value: '第四章 新标题' } })
    expect(screen.getByText('有未保存修改')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /保存草稿修改/ }))

    await waitFor(() => expect(api.put).toHaveBeenCalledWith(
      '/projects/project-1/outline-drafts/draft-1',
      expect.objectContaining({ nodes: [expect.objectContaining({ title: '第四章 新标题' })] }),
    ))
    expect(await screen.findByText('草稿已保存')).toBeInTheDocument()
    expect(screen.queryByText('有未保存修改')).not.toBeInTheDocument()
    expect(screen.getAllByText('待确认')).toHaveLength(2)
    expect(api.put).toHaveBeenCalledTimes(1)
    expect(api.post).not.toHaveBeenCalled()
  })

  it('saves a formal volume without clearing its hidden chapter-range metadata', async () => {
    mockExistingOutline()
    api.put.mockResolvedValue({ data: { data: formalVolume } })
    renderPage()
    await screen.findByRole('heading', { name: '第一卷' })
    fireEvent.change(screen.getByLabelText('标题'), { target: { value: '第一卷 修订' } })
    fireEvent.click(screen.getByRole('button', { name: /保存正式节点/ }))
    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1))
    expect(api.put.mock.calls[0][0]).toBe('/projects/project-1/outline/volume-1')
    expect(api.put.mock.calls[0][1]).toMatchObject({ title: '第一卷 修订' })
    expect(api.put.mock.calls[0][1]).not.toHaveProperty('metadata')
  })

  it('allows explicit selection of a formal node while a separate outline draft is pending', async () => {
    mockExistingOutline(pendingDraft)
    renderPage()
    await screen.findByRole('heading', { name: 'AI 大纲草稿' })
    fireEvent.click(await screen.findByText('第四章'))
    expect(screen.getByRole('heading', { name: 'AI 大纲草稿' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /保存正式节点/ })).not.toBeInTheDocument()
    fireEvent.click(await screen.findByText('第一卷'))
    await screen.findByRole('heading', { name: '第一卷' })
    expect(screen.queryByRole('heading', { name: 'AI 大纲草稿' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /保存正式节点/ })).toBeInTheDocument()
    fireEvent.click(screen.getByText('第四章'))
    await screen.findByRole('heading', { name: 'AI 大纲草稿' })
    expect(screen.queryByRole('button', { name: /保存正式节点/ })).not.toBeInTheDocument()
  })

  it('keeps failed draft edits visible for retry and never confirms them after a failed save', async () => {
    mockExistingOutline(pendingDraft)
    api.put.mockRejectedValue(new Error('草稿保存失败，请重试'))
    renderPage()
    await screen.findByRole('heading', { name: 'AI 大纲草稿' })
    fireEvent.change(screen.getByLabelText('草稿节点 1 摘要'), { target: { value: '作者修改的剧情' } })
    fireEvent.click(screen.getByRole('button', { name: /确认大纲/ }))
    expect(await screen.findByText('草稿保存失败，请重试')).toBeInTheDocument()
    expect(screen.getByLabelText('草稿节点 1 摘要')).toHaveValue('作者修改的剧情')
    expect(screen.getByText('有未保存修改')).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
    api.put.mockImplementation((_url: string, payload: Record<string, unknown>) => Promise.resolve({ data: { data: { ...pendingDraft, ...payload } } }))
    fireEvent.click(screen.getByRole('button', { name: /保存草稿修改/ }))
    expect(await screen.findByText('草稿已保存')).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('shows a confirmation conflict while preserving the saved pending draft', async () => {
    mockExistingOutline(pendingDraft)
    api.post.mockRejectedValue(new Error('正式大纲在提案生成后已变化，请重新生成后再确认'))
    renderPage()
    await screen.findByRole('heading', { name: 'AI 大纲草稿' })
    fireEvent.click(screen.getByRole('button', { name: /确认大纲/ }))
    await screen.findByText('正式大纲在提案生成后已变化，请重新生成后再确认')
    expect(screen.getByText('草稿已保存')).toBeInTheDocument()
    expect(screen.getByLabelText('草稿节点 1 摘要')).toHaveValue('待确认剧情')
    expect(screen.getAllByText('待确认')).toHaveLength(2)
    expect(api.put).not.toHaveBeenCalled()
    expect(api.post).toHaveBeenCalledWith('/projects/project-1/outline-drafts/draft-1/confirm', { write_after_confirm: false })
  })
})
