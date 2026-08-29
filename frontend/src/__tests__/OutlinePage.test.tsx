import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  delete: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}))
const openGeneratedOutlineDraft = vi.hoisted(() => vi.fn())

vi.mock('../api/client', () => ({ apiClient: api }))
vi.mock('../contexts/AiPanelContext', () => ({
  useAiPanelContext: () => ({
    refreshKey: 0,
    generatedOutlineDraft: null,
    openGeneratedOutlineDraft,
  }),
}))
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
    render(<OutlinePage projectId="project-1" />)

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
})
