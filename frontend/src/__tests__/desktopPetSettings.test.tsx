import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PET_PREFERENCE_PREVIEW_MS, useDesktopPetSettings } from '../features/desktopPet/useDesktopPetSettings'
import { DEFAULT_DESKTOP_PET_SETTINGS as defaults } from '../features/desktopPet/types'

const mocks = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), apply: vi.fn() }))
vi.mock('../shared/api/client', () => ({ apiClient: mocks }))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
function mountSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const error = vi.fn()
  const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  return { ...renderHook(() => useDesktopPetSettings(error), { wrapper }), error }
}
async function advance(ms = PET_PREFERENCE_PREVIEW_MS) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
  mocks.get.mockResolvedValue({ data: { data: defaults } })
  mocks.put.mockImplementation(async (_url, patch) => ({ data: { data: { ...defaults, ...patch } } }))
  mocks.apply.mockResolvedValue(true)
  window.pywebview = { api: { show_desktop_pet: async () => true, apply_desktop_pet_preferences: mocks.apply } }
})
afterEach(() => { vi.useRealTimers(); delete window.pywebview })

describe('desktop pet size preview and persistence', () => {
  it('previews immediately without saving and coalesces a burst to the latest size', async () => {
    const { result } = mountSettings()
    await advance(0)
    act(() => {
      result.current.previewSettings({ desktop_pet_scale: 0.9 })
      result.current.previewSettings({ desktop_pet_scale: 1 })
      result.current.previewSettings({ desktop_pet_scale: 1.2 })
    })
    expect(result.current.settings.desktop_pet_scale).toBe(1.2)
    await advance()
    expect(mocks.put).not.toHaveBeenCalled()
    expect(mocks.apply).toHaveBeenCalledTimes(1)
    expect(mocks.apply).toHaveBeenLastCalledWith(expect.objectContaining({ desktop_pet_scale: 1.2 }), false, null)
    await act(async () => { await result.current.persistSettings({ desktop_pet_scale: 1.2 }) })
    await advance()
    expect(mocks.put).toHaveBeenCalledExactlyOnceWith('/config/launcher', { desktop_pet_scale: 1.2 })
    expect(mocks.apply).toHaveBeenCalledTimes(1)
  })
  it('allows only one native request in flight and drops superseded sizes', async () => {
    const first = deferred<boolean>()
    mocks.apply.mockReturnValueOnce(first.promise)
    const { result } = mountSettings()
    await advance(0)
    act(() => result.current.previewSettings({ desktop_pet_scale: 0.9 }))
    await advance()
    act(() => result.current.previewSettings({ desktop_pet_scale: 1.1 }))
    await advance()
    act(() => result.current.previewSettings({ desktop_pet_scale: 1.35 }))
    await advance()
    expect(mocks.apply).toHaveBeenCalledTimes(1)
    await act(async () => first.resolve(true))
    await advance()
    expect(mocks.apply).toHaveBeenCalledTimes(2)
    expect(mocks.apply).toHaveBeenLastCalledWith(expect.objectContaining({ desktop_pet_scale: 1.35 }), false, null)
  })
  it('does not roll newer input back when an older save finishes; saves are serialized', async () => {
    const first = deferred<{ data: { data: typeof defaults } }>()
    mocks.put.mockReturnValueOnce(first.promise)
    const { result } = mountSettings()
    await advance(0)
    act(() => { void result.current.persistSettings({ desktop_pet_scale: 0.9 }) })
    act(() => { void result.current.persistSettings({ desktop_pet_scale: 1.3 }) })
    await advance()
    expect(mocks.put).toHaveBeenCalledTimes(1)
    await act(async () => first.resolve({ data: { data: { ...defaults, desktop_pet_scale: 0.9 } } }))
    await advance()
    expect(mocks.put).toHaveBeenCalledTimes(2)
    expect(mocks.put).toHaveBeenLastCalledWith('/config/launcher', { desktop_pet_scale: 1.3 })
    expect(result.current.settings.desktop_pet_scale).toBe(1.3)
    expect(result.current.saving).toBe(false)
    expect(mocks.apply.mock.calls.every(([settings]) => settings.desktop_pet_scale === 1.3)).toBe(true)
  })
  it('deduplicates release followed by blur and rolls back a failed save visibly', async () => {
    const { result, error } = mountSettings()
    await advance(0)
    await act(async () => {
      await result.current.persistSettings({ desktop_pet_scale: 1.2 })
      await result.current.persistSettings({ desktop_pet_scale: 1.2 })
    })
    expect(mocks.put).toHaveBeenCalledTimes(1)
    mocks.put.mockRejectedValueOnce(new Error('Disk write failed'))
    await act(async () => { await result.current.persistSettings({ desktop_pet_scale: 0.7 }) })
    await advance()
    expect(result.current.settings.desktop_pet_scale).toBe(1.2)
    expect(error).toHaveBeenLastCalledWith('Disk write failed')
    expect(mocks.apply).toHaveBeenLastCalledWith(expect.objectContaining({ desktop_pet_scale: 1.2 }), false, null)
  })
  it('preserves a newer uncommitted drag over an old save and reports a native failure', async () => {
    const first = deferred<{ data: { data: typeof defaults } }>()
    mocks.put.mockReturnValueOnce(first.promise)
    mocks.apply.mockResolvedValueOnce(false)
    const { result, error } = mountSettings()
    await advance(0)
    act(() => { void result.current.persistSettings({ desktop_pet_scale: 1 }) })
    act(() => result.current.previewSettings({ desktop_pet_scale: 1.25 }))
    await act(async () => first.resolve({ data: { data: { ...defaults, desktop_pet_scale: 1 } } }))
    await advance()
    expect(result.current.settings.desktop_pet_scale).toBe(1.25)
    expect(mocks.put).toHaveBeenCalledTimes(1)
    expect(error).toHaveBeenLastCalledWith('桌宠设置预览未能同步，请重试。')
  })
  it('ignores an initial settings read arriving after a successful save', async () => {
    const initial = deferred<{ data: { data: typeof defaults } }>()
    mocks.get.mockReturnValueOnce(initial.promise)
    const { result } = mountSettings()
    await act(async () => { await result.current.persistSettings({ desktop_pet_scale: 1.1 }) })
    await act(async () => initial.resolve({ data: { data: defaults } }))
    expect(result.current.settings.desktop_pet_scale).toBe(1.1)
  })
  it('still accepts settings from the main window but protects the local active drag', async () => {
    const { result } = mountSettings()
    await advance(0)
    const notify = (scale: number) => window.dispatchEvent(new CustomEvent('siming:desktop-pet-preferences',
      { detail: { desktop_pet_scale: scale } }))
    act(() => { notify(1.1) })
    expect(result.current.settings.desktop_pet_scale).toBe(1.1)
    act(() => { result.current.previewSettings({ desktop_pet_scale: 1.3 }); notify(1) })
    expect(result.current.settings.desktop_pet_scale).toBe(1.3)
  })
  it('cancels pending preview work on unmount', async () => {
    const { result, unmount } = mountSettings()
    await advance(0)
    act(() => result.current.previewSettings({ desktop_pet_scale: 1.3 }))
    unmount()
    await advance()
    expect(mocks.apply).not.toHaveBeenCalled()
  })
})
