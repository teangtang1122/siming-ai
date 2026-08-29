import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('../api/client', () => ({ apiClient: api }))

import PromptPacksPage from '../pages/PromptPacksPage'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

const packs = [
  {
    id: 'row-a', project_id: null, pack_id: 'pack-a', version: '1.0.0', scope: 'character_design',
    title: '角色提示词', summary: null, is_builtin: true, enabled: true, updated_at: null,
  },
  {
    id: 'row-b', project_id: null, pack_id: 'pack-b', version: '1.0.0', scope: 'worldbuilding',
    title: '世界观提示词', summary: null, is_builtin: true, enabled: true, updated_at: null,
  },
]

function detail(packId: string, title: string, systemPrompt: string) {
  return {
    pack_id: packId,
    version: '1.0.0',
    scope: packId === 'pack-a' ? 'character_design' : 'worldbuilding',
    title,
    summary: null,
    system_prompt: systemPrompt,
    workflow: null,
    quality_rubric: null,
    forbidden_patterns: null,
  }
}

describe('PromptPacksPage request ownership', () => {
  beforeEach(() => {
    api.get.mockReset()
    api.post.mockReset()
  })

  it('keeps the selected pack and editor content aligned after out-of-order details', async () => {
    const first = deferred<{ data: { data: ReturnType<typeof detail> } }>()
    const second = deferred<{ data: { data: ReturnType<typeof detail> } }>()
    api.get.mockImplementation((url: string) => {
      if (url === '/projects/project-1/prompt-packs') {
        return Promise.resolve({ data: { data: { items: packs, total: packs.length } } })
      }
      if (url.endsWith('/prompt-packs/pack-a')) return first.promise
      if (url.endsWith('/prompt-packs/pack-b')) return second.promise
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })

    render(<PromptPacksPage projectId="project-1" />)
    fireEvent.click(await screen.findByText('世界观提示词'))

    await act(async () => {
      second.resolve({ data: { data: detail('pack-b', '世界观提示词', 'PROMPT_B') } })
      await second.promise
    })
    expect(await screen.findByDisplayValue('PROMPT_B')).toBeInTheDocument()

    await act(async () => {
      first.resolve({ data: { data: detail('pack-a', '角色提示词', 'PROMPT_A') } })
      await first.promise
    })
    expect(screen.getByDisplayValue('PROMPT_B')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('PROMPT_A')).not.toBeInTheDocument()
  })
})
