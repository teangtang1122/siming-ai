import { createRef, StrictMode } from 'react'
import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PoseCanvas, type PoseCanvasHandle } from '../features/desktopPet/poses/PoseCanvas'

const mocks = vi.hoisted(() => ({ load: vi.fn(), draw: vi.fn(), dispose: vi.fn(), create: vi.fn() }))
vi.mock('../features/desktopPet/poses/contract', async importOriginal => ({
  ...await importOriginal<typeof import('../features/desktopPet/poses/contract')>(),
  loadPoseImages: mocks.load,
}))
vi.mock('../features/desktopPet/poses/renderer', () => ({
  PoseRenderer: class {
    constructor() { mocks.create() }
    draw = mocks.draw
    dispose = mocks.dispose
  },
}))
const props = { state: 'idle' as const, ambient: 'awake' as const,
  bubbleSide: 'right' as const, bubblePlacement: 'side' as const, dragging: false }
let nextFrame: FrameRequestCallback | undefined
const originalResizeObserver = window.ResizeObserver

beforeEach(() => {
  vi.clearAllMocks()
  mocks.load.mockResolvedValue(new Map())
  mocks.draw.mockImplementation(() => document.createElement('canvas'))
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    setTransform: vi.fn(), clearRect: vi.fn(), drawImage: vi.fn(),
  } as unknown as CanvasRenderingContext2D)
  vi.stubGlobal('requestAnimationFrame', vi.fn(callback => { nextFrame = callback; return 1 }))
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); window.ResizeObserver = originalResizeObserver; nextFrame = undefined })

describe('desktop pose canvas lifecycle', () => {
  it('coalesces resize notifications, paints in the same frame as bitmap reset, and preserves the renderer', async () => {
    let observerCallback: () => void = () => {}
    window.ResizeObserver = class {
      constructor(callback: ResizeObserverCallback) { observerCallback = () => callback([], this) }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    let width = 272, height = 240
    vi.spyOn(HTMLCanvasElement.prototype, 'getBoundingClientRect').mockImplementation(() => (
      { width, height } as DOMRect
    ))
    const widthSetter = vi.spyOn(HTMLCanvasElement.prototype, 'width', 'set')
    const heightSetter = vi.spyOn(HTMLCanvasElement.prototype, 'height', 'set')
    const status = vi.fn()
    render(<PoseCanvas {...props} onStatusChange={status} />)
    await waitFor(() => expect(status).toHaveBeenLastCalledWith('ready'))
    widthSetter.mockClear(); heightSetter.mockClear(); mocks.draw.mockClear()
    act(() => {
      for (let i = 0; i < 8; i++) { window.dispatchEvent(new Event('resize')); observerCallback() }
      nextFrame?.(performance.now() + 100)
    })
    expect(widthSetter).not.toHaveBeenCalled()
    expect(heightSetter).not.toHaveBeenCalled()
    act(() => {
      width = 340; height = 300
      window.dispatchEvent(new Event('resize')); observerCallback()
      width = 459; height = 405
      window.dispatchEvent(new Event('resize')); observerCallback()
    })
    expect(widthSetter).not.toHaveBeenCalled()
    const before = mocks.draw.mock.calls.length
    act(() => nextFrame?.(performance.now() + 101))
    expect(widthSetter).toHaveBeenCalledExactlyOnceWith(459)
    expect(heightSetter).toHaveBeenCalledExactlyOnceWith(405)
    expect(mocks.draw).toHaveBeenCalledTimes(before + 1)
    expect(mocks.create).toHaveBeenCalledTimes(1)
    expect(mocks.load).toHaveBeenCalledTimes(1)
  })
  it('switches to the lifted sprite during drag and back to the live task without reloading textures', async () => {
    const status = vi.fn()
    const view = render(<PoseCanvas {...props} state="writing" onStatusChange={status} />)
    await waitFor(() => expect(status).toHaveBeenLastCalledWith('ready'))
    view.rerender(<PoseCanvas {...props} state="writing" dragging onStatusChange={status} />)
    act(() => nextFrame?.(performance.now() + 100))
    expect(document.querySelector('canvas[data-pose]')).toHaveAttribute('data-pose', 'picked_up')
    expect(mocks.draw.mock.calls[mocks.draw.mock.calls.length - 1]?.[0].id).toBe('picked_up')
    view.rerender(<PoseCanvas {...props} state="writing" onStatusChange={status} />)
    act(() => nextFrame?.(performance.now() + 200))
    expect(document.querySelector('canvas[data-pose]')).toHaveAttribute('data-pose', 'reading')
    expect(mocks.draw.mock.calls[mocks.draw.mock.calls.length - 1]?.[0].id).toBe('reading')
    expect(mocks.load).toHaveBeenCalledTimes(1)
  })
  it('loads once, forwards interactions, changes poses without texture reload, then releases the renderer', async () => {
    const ref = createRef<PoseCanvasHandle>()
    const status = vi.fn()
    const view = render(<PoseCanvas {...props} ref={ref} onStatusChange={status} />)
    await waitFor(() => expect(status).toHaveBeenLastCalledWith('ready'))
    expect(mocks.draw.mock.calls[0][0].id).toBe('standing')
    act(() => { ref.current?.lookAt(1, 1); ref.current?.tap() })
    view.rerender(<PoseCanvas {...props} state="writing" ref={ref} onStatusChange={status} />)
    act(() => nextFrame?.(performance.now() + 100))
    expect(mocks.draw.mock.calls[mocks.draw.mock.calls.length - 1]?.[0].id).toBe('reading')
    expect(mocks.load).toHaveBeenCalledTimes(1)
    act(() => ref.current?.resetLook())
    view.unmount()
    expect(mocks.dispose).toHaveBeenCalledTimes(1)
    expect(mocks.load.mock.calls[0][0].aborted).toBe(true)
  })
  it('does not create a renderer after unmount while assets are loading', async () => {
    let finish: (value: Map<string, HTMLImageElement>) => void = () => {}
    mocks.load.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const view = render(<PoseCanvas {...props} />)
    view.unmount()
    await act(async () => finish(new Map()))
    expect(mocks.create).not.toHaveBeenCalled()
  })
  it('does not leak a duplicate renderer under React StrictMode', async () => {
    const view = render(<StrictMode><PoseCanvas {...props} /></StrictMode>)
    await waitFor(() => expect(mocks.create).toHaveBeenCalledTimes(1))
    expect(mocks.load.mock.calls[0][0].aborted).toBe(true)
    view.unmount()
    expect(mocks.dispose).toHaveBeenCalledTimes(1)
  })
  it('reports missing art without silently restoring the retired model', async () => {
    mocks.load.mockRejectedValue(new Error('missing eye frame'))
    const status = vi.fn()
    render(<PoseCanvas {...props} onStatusChange={status} />)
    await waitFor(() => expect(status).toHaveBeenLastCalledWith('error'))
    expect(screen.getByText('司命素材加载失败')).toHaveAttribute('title', 'missing eye frame')
    expect(mocks.create).not.toHaveBeenCalled()
  })
  it('reports render failure and stops the loop', async () => {
    mocks.draw.mockImplementation(() => { throw new Error('context lost') })
    const status = vi.fn()
    render(<PoseCanvas {...props} onStatusChange={status} />)
    await waitFor(() => expect(status).toHaveBeenLastCalledWith('error'))
    expect(mocks.dispose).toHaveBeenCalledTimes(1)
    expect(requestAnimationFrame).not.toHaveBeenCalled()
  })
})
