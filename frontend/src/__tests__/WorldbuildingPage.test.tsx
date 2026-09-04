import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
vi.mock('../hooks/useUnsavedGuard', () => ({
  useUnsavedGuard: () => ({
    ...unsaved,
    saveError: '',
    saveStatus: 'saved',
  }),
}))

import WorldbuildingPage from '../pages/WorldbuildingPage'

const currentEntry = {
  id: 'world-current',
  project_id: 'project-1',
  dimension: 'factions',
  title: '临汐水文站',
  content: '当前版本设定',
  status: 'active',
  sort_order: 0,
  created_at: '2026-08-31T00:00:00Z',
  updated_at: '2026-08-31T00:00:00Z',
}

const supersededEntry = {
  ...currentEntry,
  id: 'world-old',
  title: '水文站',
  content: '旧版错误设定',
  status: 'superseded',
  sort_order: 1,
}

function response(entries: typeof currentEntry[]) {
  return {
    data: {
      data: {
        grouped: { factions: entries },
        total: entries.length,
      },
    },
  }
}

describe('WorldbuildingPage', () => {
  beforeEach(() => {
    api.delete.mockReset()
    api.get.mockReset()
    api.post.mockReset()
    api.put.mockReset()
    api.put.mockResolvedValue({ data: { data: supersededEntry } })
    api.get.mockImplementation((url: string) => Promise.resolve(
      url.endsWith('?include_inactive=true')
        ? response([currentEntry, supersededEntry])
        : response([currentEntry]),
    ))
  })

  it('hides stale entries by default and lets the author inspect and restore them', async () => {
    render(<WorldbuildingPage projectId="project-1" />)

    fireEvent.click(await screen.findByText('势力'))
    expect(await screen.findByText('临汐水文站')).toBeInTheDocument()
    expect(screen.queryByText('水文站')).not.toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith('/projects/project-1/worldbuilding')

    fireEvent.click(screen.getByRole('switch', { name: '显示历史条目' }))

    expect(await screen.findByText('水文站')).toBeInTheDocument()
    expect(screen.getByText('旧版')).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith(
      '/projects/project-1/worldbuilding?include_inactive=true',
    )

    fireEvent.click(screen.getByRole('button', { name: '恢复水文站' }))
    await waitFor(() => expect(api.put).toHaveBeenCalledWith(
      '/projects/project-1/worldbuilding/world-old',
      { status: 'active' },
    ))
  })
})
