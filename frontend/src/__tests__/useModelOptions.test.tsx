import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockGet = vi.hoisted(() => vi.fn())

vi.mock('../api/client', () => ({
  apiClient: { get: mockGet },
}))

import { useModelOptions } from '../hooks/useModelOptions'

describe('useModelOptions readiness filtering', () => {
  beforeEach(() => vi.clearAllMocks())

  it('exposes only verified ready models and keeps the ready global default', async () => {
    mockGet.mockResolvedValue({ data: { data: { items: [
      {
        id: 'claude-detected', provider: 'claude_cli', default_model: 'claude-cli',
        is_global_default: false, readiness_status: 'detected', is_usable: false,
      },
      {
        id: 'opencode-ready', provider: 'opencode_cli', default_model: 'opencode/free-model',
        is_global_default: true, readiness_status: 'ready', is_usable: true,
      },
    ], total: 2 } } })

    const { result } = renderHook(() => useModelOptions())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.modelOptions).toHaveLength(1)
    expect(result.current.modelOptions[0].value).toBe('opencode_cli:opencode/free-model')
    expect(result.current.defaultModel).toBe('opencode_cli:opencode/free-model')
    expect(result.current.hasDetectedModels).toBe(true)
  })

  it('does not fall back to the first detected CLI', async () => {
    mockGet.mockResolvedValue({ data: { data: { items: [{
      id: 'claude-detected', provider: 'claude_cli', default_model: 'claude-cli',
      is_global_default: false, readiness_status: 'detected', is_usable: false,
    }], total: 1 } } })

    const { result } = renderHook(() => useModelOptions())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.modelOptions).toEqual([])
    expect(result.current.defaultModel).toBeUndefined()
    expect(result.current.hasModels).toBe(false)
    expect(result.current.hasDetectedModels).toBe(true)
  })
})
