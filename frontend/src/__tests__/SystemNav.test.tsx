import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('../api/client', () => ({ apiClient: api }))

import SystemNav from '../components/SystemNav'

describe('SystemNav', () => {
  beforeEach(() => vi.clearAllMocks())

  it('stops emphasizing quick start after AI is ready', async () => {
    api.get.mockResolvedValue({ data: { data: { has_usable_models: true, needs_setup: false } } })

    render(<MemoryRouter initialEntries={['/dashboard']}><SystemNav current="dashboard" /></MemoryRouter>)

    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: '快速开始' })).not.toBeInTheDocument()
  })

  it('keeps quick start available when no usable model exists', async () => {
    api.get.mockResolvedValue({ data: { data: { has_usable_models: false, needs_setup: true } } })

    render(<MemoryRouter initialEntries={['/dashboard']}><SystemNav current="dashboard" /></MemoryRouter>)

    expect(await screen.findByRole('button', { name: '快速开始' })).toBeInTheDocument()
  })
})
