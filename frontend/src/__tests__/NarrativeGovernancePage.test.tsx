import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn() }))
vi.mock('../api/client', () => ({ apiClient: api }))

import NarrativeGovernancePage from '../pages/NarrativeGovernancePage'

const emptyDashboard = {
  foreshadowings: [], causal_edges: [], narrative_debts: [], character_states: [], quality_metrics: [], checkpoints: [],
  counts: { open_foreshadowings: 0, open_causal_edges: 0, open_debts: 0 },
}

describe('NarrativeGovernancePage', () => {
  beforeEach(() => {
    api.get.mockReset()
    api.post.mockReset()
    api.patch.mockReset()
  })

  it('renders the empty governance state', async () => {
    api.get.mockResolvedValue({ data: { data: emptyDashboard } })
    render(<NarrativeGovernancePage projectId="p1" />)
    expect(await screen.findByText('当前没有结构化治理项')).toBeInTheDocument()
    expect(screen.getByText('开放伏笔')).toBeInTheDocument()
  })

  it('switches to the high-risk filter', async () => {
    api.get.mockResolvedValue({ data: { data: emptyDashboard } })
    render(<NarrativeGovernancePage projectId="p1" />)
    await screen.findByText('当前没有结构化治理项')
    fireEvent.click(screen.getByText('高风险'))
    await waitFor(() => expect(api.get).toHaveBeenLastCalledWith('/projects/p1/narrative-governance', { view: 'risk' }))
  })

  it('marks a foreshadowing fulfilled', async () => {
    api.get.mockResolvedValue({ data: { data: { ...emptyDashboard, foreshadowings: [{ id: 'f1', title: '断剑血纹', status: 'open', importance: 'high' }], counts: { ...emptyDashboard.counts, open_foreshadowings: 1 } } } })
    api.patch.mockResolvedValue({ data: { code: 0 } })
    render(<NarrativeGovernancePage projectId="p1" />)
    await screen.findByText('断剑血纹')
    fireEvent.click(screen.getByTitle('标记兑现'))
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/projects/p1/narrative-governance/items/foreshadowings/f1', { status: 'fulfilled' }))
  })
})
