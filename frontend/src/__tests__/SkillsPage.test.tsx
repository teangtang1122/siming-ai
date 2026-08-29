import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  delete: vi.fn(), get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn(),
}))
vi.mock('../api/client', () => ({ apiClient: api }))

import SkillsPage from '../pages/SkillsPage'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

function skill(id: string, name: string) {
  return {
    id,
    project_id: 'project-1',
    builtin_key: null,
    name,
    description: `${name}说明`,
    trigger_examples: [],
    system_prompt: `${name}提示词`,
    recommended_tools: [],
    forbidden_tools: [],
    scope: 'project',
    priority: 0,
    enabled: true,
    is_builtin: false,
    created_at: '2026-08-29T00:00:00Z',
    updated_at: '2026-08-29T00:00:00Z',
  }
}

describe('SkillsPage version ownership', () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset())
  })

  it('does not let an older skill version request overwrite the current drawer', async () => {
    const first = deferred<{ data: { data: { items: unknown[]; total: number } } }>()
    const second = deferred<{ data: { data: { items: unknown[]; total: number } } }>()
    api.get.mockImplementation((url: string) => {
      if (url === '/projects/project-1/skills') {
        return Promise.resolve({ data: { data: { items: [skill('skill-a', '技能A'), skill('skill-b', '技能B')], total: 2 } } })
      }
      if (url.endsWith('/skills/templates') || url.endsWith('/skills/tools')) {
        return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      }
      if (url.endsWith('/skills/skill-a/versions')) return first.promise
      if (url.endsWith('/skills/skill-b/versions')) return second.promise
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })

    render(<SkillsPage projectId="project-1" />)
    await screen.findByText('技能B')
    const historyButtons = Array.from(document.querySelectorAll('.anticon-history'))
      .map((icon) => icon.closest('button'))
      .filter((button): button is HTMLButtonElement => Boolean(button))
    expect(historyButtons).toHaveLength(2)
    fireEvent.click(historyButtons[0])
    fireEvent.click(historyButtons[1])

    await act(async () => {
      second.resolve({ data: { data: { items: [{
        id: 'version-b', skill_id: 'skill-b', project_id: 'project-1', title: 'B 当前版本',
        change_summary: 'B 变更', snapshot: {}, created_at: '2026-08-29T01:00:00Z',
      }], total: 1 } } })
      await second.promise
    })
    expect(await screen.findByText('版本历史：技能B')).toBeInTheDocument()
    expect(screen.getByText('B 当前版本')).toBeInTheDocument()

    await act(async () => {
      first.resolve({ data: { data: { items: [{
        id: 'version-a', skill_id: 'skill-a', project_id: 'project-1', title: 'A 迟到版本',
        change_summary: 'A 变更', snapshot: {}, created_at: '2026-08-29T00:30:00Z',
      }], total: 1 } } })
      await first.promise
    })
    expect(screen.getByText('版本历史：技能B')).toBeInTheDocument()
    expect(screen.queryByText('A 迟到版本')).not.toBeInTheDocument()
  })
})
