import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../shared/api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

import { apiClient } from '../shared/api/client'
import { createProject, listProjects } from '../features/projects'
import { updateOperationInCache } from '../features/operations'
import type { OperationRun } from '../features/operations'

const mockedApi = vi.mocked(apiClient)

function operation(id: string, title = id): OperationRun {
  return {
    id,
    source_kind: 'test',
    title,
    status: 'running',
    health_status: 'active',
    progress: { mode: 'indeterminate' },
    can_pause: false,
    can_cancel: true,
    can_retry: false,
    elapsed_seconds: 1,
  }
}

describe('feature query contracts', () => {
  beforeEach(() => vi.clearAllMocks())

  it('normalizes project defaults before using the generated request contract', async () => {
    mockedApi.post.mockResolvedValue({
      data: { data: { id: 'p1', title: '雾海拾光' } },
    } as never)

    await createProject({ title: '雾海拾光' })

    expect(mockedApi.post).toHaveBeenCalledWith('/projects', expect.objectContaining({
      title: '雾海拾光',
      daily_word_goal: 6000,
      narrative_perspective: 'third_person',
      short_sentences: false,
      writing_style: 'natural',
    }))
  })

  it('passes the project search through one feature API', async () => {
    mockedApi.get.mockResolvedValue({ data: { data: { items: [], total: 0 } } } as never)
    await listProjects('雾海')
    expect(mockedApi.get).toHaveBeenCalledWith('/projects', { q: '雾海' })
  })

  it('updates SSE operation projections without duplicating entries', () => {
    const current = [operation('one'), operation('two')]
    const updated = updateOperationInCache(current, operation('two', '已推进'))
    expect(updated).toHaveLength(2)
    expect(updated[1].title).toBe('已推进')
  })
})
