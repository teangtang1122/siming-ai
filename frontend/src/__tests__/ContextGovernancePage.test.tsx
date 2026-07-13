import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('../api/client', () => ({ apiClient: api }))

import ContextGovernancePage from '../pages/ContextGovernancePage'

const manifest = {
  id: 'm1',
  task_type: 'writing',
  model: 'test-model',
  execution_route: 'external_mcp',
  status: 'needs_confirmation',
  warnings: ['Target outline is missing.'],
  coverage: {
    target_outline: { required: true, status: 'missing', item_count: 0, reason: 'Select an outline node.' },
  },
  budget: {
    context_window_tokens: 16384,
    input_budget_tokens: 8500,
    output_reserve_tokens: 7372,
    safety_margin_tokens: 512,
    estimated_input_tokens: 320,
    estimated_input_chars: 1100,
    remaining_input_tokens: 8180,
  },
  items: [{
    id: 'mi1',
    category: 'style',
    source_type: 'project_style',
    source_id: 'p1',
    source_hash: 'hash-1',
    title: 'Project style',
    required: true,
    pinned: false,
    estimated_tokens: 320,
    selection_reason: 'Required style anchor.',
    scores: { final: 1 },
    content: 'Use a restrained first-person voice.',
  }],
}

function mockLoad({ generationAllowed = true } = {}) {
  api.get.mockImplementation((url: string) => {
    if (url.includes('context-governance-status')) {
      return Promise.resolve({ data: { data: { generation_allowed: generationAllowed, reason: generationAllowed ? '' : 'Rebuild pending', semantic: { available: false, reason: 'FastEmbed unavailable' } } } })
    }
    if (url.endsWith('/context-manifests')) {
      return Promise.resolve({ data: { data: { items: [manifest] } } })
    }
    if (url.includes('/context-manifests/m1')) {
      return Promise.resolve({ data: { data: manifest } })
    }
    return Promise.resolve({ data: { data: { items: [] } } })
  })
}

describe('ContextGovernancePage', () => {
  beforeEach(() => {
    api.get.mockReset()
    api.post.mockReset()
  })

  it('shows rebuild maintenance state and the governed budget', async () => {
    mockLoad({ generationAllowed: false })
    render(<ContextGovernancePage projectId="p1" />)

    expect(await screen.findByText('Rebuild pending')).toBeInTheDocument()
    expect(screen.getByText('writing')).toBeInTheDocument()
    expect(screen.getByText('320 / 8500 tokens')).toBeInTheDocument()
  })

  it('opens the persisted source audit trail', async () => {
    mockLoad()
    render(<ContextGovernancePage projectId="p1" />)

    await screen.findByText('writing')
    const view = document.querySelector('.anticon-eye')?.closest('button')
    expect(view).not.toBeNull()
    fireEvent.click(view as HTMLButtonElement)

    expect(await screen.findByText('Project style')).toBeInTheDocument()
    expect(screen.getByText('Required style anchor.')).toBeInTheDocument()
    expect(screen.getByText(/hash: hash-1/)).toBeInTheDocument()
  })

  it('requires and submits an override reason', async () => {
    mockLoad()
    api.post.mockResolvedValue({ data: { code: 0 } })
    render(<ContextGovernancePage projectId="p1" />)

    await screen.findByText('writing')
    const override = document.querySelector('.anticon-safety-certificate')?.closest('button')
    expect(override).not.toBeNull()
    fireEvent.click(override as HTMLButtonElement)

    const reason = await screen.findByRole('textbox')
    fireEvent.change(reason, { target: { value: 'The author has supplied the missing outline separately.' } })
    const confirm = document.querySelector('.ant-modal-footer .ant-btn-primary') as HTMLButtonElement
    fireEvent.click(confirm)

    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/projects/p1/context-manifests/m1/override',
      { reason: 'The author has supplied the missing outline separately.', actor: 'author' },
    ))
  })
})
