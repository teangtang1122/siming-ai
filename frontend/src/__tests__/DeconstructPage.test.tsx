import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ get: vi.fn(), stream: vi.fn() }))
vi.mock('../api/client', () => ({ apiClient: api }))
vi.mock('../hooks/useModelOptions', () => ({
  useModelOptions: () => ({ defaultModel: 'openai:test', loading: false, modelOptions: [] }),
}))

import DeconstructPage from '../pages/DeconstructPage'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

function report(id: string, totalWords: number, hook: string) {
  return {
    id,
    title: `${id}报告`,
    status: 'completed',
    phase: 'completed',
    golden_three: { hook, protagonist_goal: '', core_conflict: '', reader_expectation: '' },
    plot_nodes: [],
    characters: [],
    worldbuilding_entries: [],
    highlights: [],
    rhythm_curve: [],
    patterns: [],
    reduce_sections: [],
    reduce_errors: {},
    chunk_results: [],
    logs: [],
    total_chunks: 1,
    completed_chunks: 1,
    failed_chunks: 0,
    total_words: totalWords,
    created_at: '2026-08-29T00:00:00Z',
  }
}

describe('DeconstructPage report ownership', () => {
  beforeEach(() => {
    api.get.mockReset()
    api.stream.mockReset()
  })

  it('keeps the last selected report when an older detail arrives late', async () => {
    const first = deferred<{ data: { data: ReturnType<typeof report> } }>()
    const second = deferred<{ data: { data: ReturnType<typeof report> } }>()
    api.get.mockImplementation((url: string) => {
      if (url.endsWith('/deconstruct/preview')) {
        return Promise.resolve({ data: { data: {
          chapters: [], total_chapters: 0, total_words: 0, can_deconstruct: false, combined_text: '',
        } } })
      }
      if (url.endsWith('/deconstruct/reports')) {
        return Promise.resolve({ data: { data: { items: [
          { id: 'report-a', title: 'A', status: 'failed', phase: 'failed', total_words: 1111, created_at: '2026-08-29T00:00:00Z' },
          { id: 'report-b', title: 'B', status: 'failed', phase: 'failed', total_words: 2222, created_at: '2026-08-29T00:01:00Z' },
        ], total: 2 } } })
      }
      if (url.endsWith('/deconstruct/report-a')) return first.promise
      if (url.endsWith('/deconstruct/report-b')) return second.promise
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })

    render(<DeconstructPage projectId="project-1" />)
    fireEvent.click(await screen.findByRole('button', { name: /1,111字/ }))
    fireEvent.click(screen.getByRole('button', { name: /2,222字/ }))

    await act(async () => {
      second.resolve({ data: { data: report('report-b', 2222, 'B 当前结果') } })
      await second.promise
    })
    expect(await screen.findByText('B 当前结果')).toBeInTheDocument()

    await act(async () => {
      first.resolve({ data: { data: report('report-a', 1111, 'A 迟到结果') } })
      await first.promise
    })
    expect(screen.getByText('B 当前结果')).toBeInTheDocument()
    expect(screen.queryByText('A 迟到结果')).not.toBeInTheDocument()
  })
})
