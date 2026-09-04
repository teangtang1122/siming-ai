import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  delete: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}))

const unsaved = vi.hoisted(() => ({
  confirmLeave: (action: () => void) => action(),
  markDirty: vi.fn(),
  markSaved: vi.fn(),
  markSaveFailed: vi.fn(),
  markSaving: vi.fn(),
}))

vi.mock('../api/client', () => ({ apiClient: api }))
vi.mock('../contexts/AiPanelContext', () => ({ useAiPanelContext: () => ({ refreshKey: 0 }) }))
vi.mock('../hooks/useModelOptions', () => ({ useModelOptions: () => ({ modelOptions: [], loading: false }) }))
vi.mock('../hooks/useUnsavedGuard', () => ({
  useUnsavedGuard: () => ({
    ...unsaved,
    saveError: '',
    saveStatus: 'saved',
  }),
}))

import CharactersPage from '../pages/CharactersPage'

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

const characterSummary = {
  id: 'character-a',
  project_id: 'project-1',
  name: '角色甲',
  abilities: [],
  current_version: 1,
  is_evolution_tracked: true,
  created_at: '2026-08-29T00:00:00Z',
  updated_at: '2026-08-29T00:00:00Z',
}

const characterDetail = {
  ...characterSummary,
  appearance: '旧角色外貌',
  personality: '旧角色性格',
  background: '旧角色背景',
  aliases: [],
  appearances: { outline_nodes: [], chapters: [] },
}

describe('CharactersPage', () => {
  beforeEach(() => {
    api.delete.mockReset()
    api.get.mockReset()
    api.post.mockReset()
    api.put.mockReset()
    Object.values(unsaved).forEach((mock) => {
      if (typeof mock === 'function' && 'mockClear' in mock) mock.mockClear()
    })
  })

  it('keeps an explicit new-character form when the old detail response arrives late', async () => {
    const oldDetail = deferred<{ data: { data: typeof characterDetail } }>()
    api.get.mockImplementation((url: string) => {
      if (url === '/projects/project-1/characters') {
        return Promise.resolve({ data: { data: { items: [characterSummary], total: 1 } } })
      }
      if (url === '/projects/project-1/characters/relationships') {
        return Promise.resolve({ data: { data: { nodes: [], edges: [], total: 0 } } })
      }
      if (url === '/projects/project-1/characters/character-a') return oldDetail.promise
      if (url.endsWith('/versions')) return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      if (url.endsWith('/ai-config')) return Promise.reject(new Error('not configured'))
      throw new Error(`Unexpected GET ${url}`)
    })

    render(<CharactersPage projectId="project-1" />)

    await screen.findByText('角色甲')
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/projects/project-1/characters/character-a'))

    const addButton = document.querySelector('.anticon-plus')?.closest('button')
    expect(addButton).not.toBeNull()
    fireEvent.click(addButton as HTMLButtonElement)

    expect(screen.getByRole('heading', { name: '新角色' })).toBeInTheDocument()
    expect(screen.getByLabelText('姓名')).toHaveValue('')

    await act(async () => {
      oldDetail.resolve({ data: { data: characterDetail } })
      await oldDetail.promise
    })

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: '新角色' })).toBeInTheDocument()
      expect(screen.getByLabelText('姓名')).toHaveValue('')
    })
    expect(screen.queryByDisplayValue('角色甲')).not.toBeInTheDocument()
  })

  it('keeps character B selected when character A detail resolves last', async () => {
    const secondSummary = { ...characterSummary, id: 'character-b', name: '角色乙' }
    const firstDetail = deferred<{ data: { data: typeof characterDetail } }>()
    const secondDetail = deferred<{ data: { data: typeof characterDetail } }>()
    api.get.mockImplementation((url: string) => {
      if (url === '/projects/project-1/characters') {
        return Promise.resolve({ data: { data: { items: [characterSummary, secondSummary], total: 2 } } })
      }
      if (url === '/projects/project-1/characters/relationships') {
        return Promise.resolve({ data: { data: { nodes: [], edges: [], total: 0 } } })
      }
      if (url === '/projects/project-1/characters/character-a') return firstDetail.promise
      if (url === '/projects/project-1/characters/character-b') return secondDetail.promise
      if (url.endsWith('/versions')) return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      if (url.endsWith('/ai-config')) return Promise.reject(new Error('not configured'))
      throw new Error(`Unexpected GET ${url}`)
    })

    render(<CharactersPage projectId="project-1" />)
    fireEvent.click(await screen.findByText('角色乙'))

    await act(async () => {
      secondDetail.resolve({ data: { data: { ...characterDetail, id: 'character-b', name: '角色乙' } } })
      await secondDetail.promise
    })
    expect(await screen.findByDisplayValue('角色乙')).toBeInTheDocument()

    await act(async () => {
      firstDetail.resolve({ data: { data: characterDetail } })
      await firstDetail.promise
    })
    expect(screen.getByDisplayValue('角色乙')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('角色甲')).not.toBeInTheDocument()
  })

  it('creates with POST after entering the new-character state', async () => {
    api.get.mockImplementation((url: string) => {
      if (url === '/projects/project-1/characters') {
        return Promise.resolve({ data: { data: { items: [characterSummary], total: 1 } } })
      }
      if (url === '/projects/project-1/characters/relationships') {
        return Promise.resolve({ data: { data: { nodes: [], edges: [], total: 0 } } })
      }
      if (url === '/projects/project-1/characters/character-a') {
        return Promise.resolve({ data: { data: characterDetail } })
      }
      if (url === '/projects/project-1/characters/character-b') {
        return Promise.resolve({ data: { data: { ...characterDetail, id: 'character-b', name: '角色乙' } } })
      }
      if (url.endsWith('/versions')) return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      if (url.endsWith('/ai-config')) return Promise.reject(new Error('not configured'))
      throw new Error(`Unexpected GET ${url}`)
    })
    api.post.mockResolvedValue({ data: { data: { ...characterDetail, id: 'character-b', name: '角色乙' } } })

    render(<CharactersPage projectId="project-1" />)
    await screen.findByDisplayValue('角色甲')

    fireEvent.click(document.querySelector('.anticon-plus')?.closest('button') as HTMLButtonElement)
    fireEvent.change(screen.getByLabelText('姓名'), { target: { value: '角色乙' } })
    fireEvent.click(screen.getByRole('button', { name: /保存角色/ }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/projects/project-1/characters',
      expect.objectContaining({ name: '角色乙', role_type: 'supporting' }),
    ))
    expect(api.put).not.toHaveBeenCalledWith(
      '/projects/project-1/characters/character-a',
      expect.anything(),
    )
  })

  it('promotes a created role identity without discarding edits made during the POST', async () => {
    const createRequest = deferred<{ data: { data: typeof characterDetail } }>()
    api.get.mockImplementation((url: string) => {
      if (url === '/projects/project-1/characters') {
        return Promise.resolve({ data: { data: { items: [characterSummary], total: 1 } } })
      }
      if (url === '/projects/project-1/characters/relationships') {
        return Promise.resolve({ data: { data: { nodes: [], edges: [], total: 0 } } })
      }
      if (url === '/projects/project-1/characters/character-a') {
        return Promise.resolve({ data: { data: characterDetail } })
      }
      if (url.endsWith('/versions')) return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      if (url.endsWith('/ai-config')) return Promise.reject(new Error('not configured'))
      throw new Error(`Unexpected GET ${url}`)
    })
    api.post.mockReturnValue(createRequest.promise)
    api.put.mockImplementation((_url: string, payload: Record<string, unknown>) => Promise.resolve({
      data: { data: { ...characterDetail, id: 'character-b', name: payload.name } },
    }))

    render(<CharactersPage projectId="project-1" />)
    await screen.findByDisplayValue('角色甲')
    fireEvent.click(document.querySelector('.anticon-plus')?.closest('button') as HTMLButtonElement)
    fireEvent.change(screen.getByLabelText('姓名'), { target: { value: '角色乙' } })
    fireEvent.click(screen.getByRole('button', { name: /保存角色/ }))
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1))

    fireEvent.change(screen.getByLabelText('姓名'), { target: { value: '角色乙·修订' } })
    await act(async () => {
      createRequest.resolve({ data: { data: { ...characterDetail, id: 'character-b', name: '角色乙' } } })
      await createRequest.promise
    })
    expect(screen.getByLabelText('姓名')).toHaveValue('角色乙·修订')

    fireEvent.click(screen.getByRole('button', { name: /保存角色/ }))
    await waitFor(() => expect(api.put).toHaveBeenCalledWith(
      '/projects/project-1/characters/character-b',
      expect.objectContaining({ name: '角色乙·修订' }),
    ))
    expect(api.post).toHaveBeenCalledTimes(1)
  })

  it('keeps the new-character form and surfaces a rejected POST', async () => {
    api.get.mockImplementation((url: string) => {
      if (url === '/projects/project-1/characters') {
        return Promise.resolve({ data: { data: { items: [characterSummary], total: 1 } } })
      }
      if (url === '/projects/project-1/characters/relationships') {
        return Promise.resolve({ data: { data: { nodes: [], edges: [], total: 0 } } })
      }
      if (url === '/projects/project-1/characters/character-a') {
        return Promise.resolve({ data: { data: characterDetail } })
      }
      if (url.endsWith('/versions')) return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      if (url.endsWith('/ai-config')) return Promise.reject(new Error('not configured'))
      throw new Error(`Unexpected GET ${url}`)
    })
    api.post.mockRejectedValue(new Error('角色创建失败'))

    render(<CharactersPage projectId="project-1" />)
    await screen.findByDisplayValue('角色甲')
    fireEvent.click(document.querySelector('.anticon-plus')?.closest('button') as HTMLButtonElement)
    fireEvent.change(screen.getByLabelText('姓名'), { target: { value: '未保存角色' } })
    fireEvent.click(screen.getByRole('button', { name: /保存角色/ }))

    await waitFor(() => expect(unsaved.markSaveFailed).toHaveBeenCalledWith('角色创建失败'))
    expect(await screen.findByText('角色创建失败')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '新角色' })).toBeInTheDocument()
    expect(screen.getByLabelText('姓名')).toHaveValue('未保存角色')
    expect(screen.getByRole('status')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: /保存角色/ })).not.toBeDisabled())
  })
})
