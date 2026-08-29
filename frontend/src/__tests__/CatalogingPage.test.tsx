import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn(), post: vi.fn(), stream: vi.fn() }))
vi.mock('../api/client', () => ({ apiClient: api }))
vi.mock('../hooks/useModelOptions', () => ({
  useModelOptions: () => ({ defaultModel: 'openai:test', loading: false, modelOptions: [] }),
}))

import CatalogingPage from '../pages/CatalogingPage'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

function job(id: string, model: string) {
  return {
    id,
    project_id: 'project-1',
    status: 'running',
    execution_mode: 'auto',
    model,
    effective_model: model,
    model_source: 'task',
    provider: 'openai',
    total_chapters: 1,
    completed_chapters: 0,
    failed_chapters: 0,
    current_chapter_id: `chapter-${id}`,
    blocked_chapter_id: null,
    error: null,
  }
}

function run(id: string, jobId: string, title: string) {
  return {
    id,
    job_id: jobId,
    chapter_id: `chapter-${jobId}`,
    chapter_order: 1,
    chapter_title: title,
    status: 'running',
  }
}

describe('CatalogingPage job ownership', () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset())
    api.stream.mockImplementation(() => undefined)
  })

  it('does not mix an older job response into the last selected task', async () => {
    const first = deferred<{ data: { data: { job: ReturnType<typeof job>; runs: ReturnType<typeof run>[] } } }>()
    const second = deferred<{ data: { data: { job: ReturnType<typeof job>; runs: ReturnType<typeof run>[] } } }>()
    const jobs = [job('job-a', 'model-a'), job('job-b', 'model-b')]
    api.get.mockImplementation((url: string) => {
      if (url.endsWith('/chapters')) return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      if (url.endsWith('/cataloging/jobs')) return Promise.resolve({ data: { data: { items: jobs, total: 2 } } })
      if (url.endsWith('/cataloging/job-a')) return first.promise
      if (url.endsWith('/cataloging/job-b')) return second.promise
      if (url.endsWith('/candidates') || url.endsWith('/facts')) {
        return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      }
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })

    render(<CatalogingPage projectId="project-1" />)
    const loadButtons = await screen.findAllByRole('button', { name: '载入任务' })
    fireEvent.click(loadButtons[0])
    fireEvent.click(loadButtons[1])

    await act(async () => {
      second.resolve({ data: { data: { job: jobs[1], runs: [run('run-b', 'job-b', 'B 章节')] } } })
      await second.promise
    })
    expect(await screen.findByText('model-b · task')).toBeInTheDocument()
    expect(screen.getByText('B 章节')).toBeInTheDocument()

    await act(async () => {
      first.resolve({ data: { data: { job: jobs[0], runs: [run('run-a', 'job-a', 'A 迟到章节')] } } })
      await first.promise
    })
    expect(screen.getByText('model-b · task')).toBeInTheDocument()
    expect(screen.queryByText('model-a · task')).not.toBeInTheDocument()
    expect(screen.queryByText('A 迟到章节')).not.toBeInTheDocument()
  })
})
