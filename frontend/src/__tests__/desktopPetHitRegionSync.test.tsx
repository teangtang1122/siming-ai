import { useEffect, useRef } from 'react'
import { act, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useDesktopPetHitRegion } from '../features/desktopPet/useDesktopPetHitRegion'
import type { NativeHitRegion } from '../features/desktopPet/hitRegion'

const update = vi.fn<(region: NativeHitRegion) => Promise<boolean>>()
const error = vi.fn()
function Harness({ menu = false, artWidth = 100, speech = true }) {
  const root = useRef<HTMLElement>(null)
  const setCharacter = useDesktopPetHitRegion(root, error)
  useEffect(() => { setCharacter({ width: artWidth, height: 100, rects: [[10, 20, 20, 60]] }) }, [setCharacter, artWidth])
  return <main ref={root}>{speech && <section className="desktop-pet__speech">你好呀</section>}
    {menu && <section className="desktop-pet__menu">小动作</section>}</main>
}

beforeEach(() => {
  vi.clearAllMocks()
  update.mockResolvedValue(true)
  window.pywebview = { api: { show_desktop_pet: async () => true, update_desktop_pet_hit_region: update } }
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function(this: HTMLElement) {
    const [x, y, width, height] = this.tagName === 'MAIN' ? [0, 0, 100, 100]
      : this.className === 'desktop-pet__menu' ? [5, 5, 90, 90] : [60, 10, 30, 20]
    return { x, y, width, height, left: x, top: y, right: x + width, bottom: y + height, toJSON() {} }
  })
})
afterEach(() => { vi.restoreAllMocks(); delete window.pywebview })

describe('native hit region synchronization', () => {
  it('releases the bubble area after speech unmounts without losing character or menu input', async () => {
    const view = render(<Harness speech={false} />)
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1))
    expect(update.mock.calls[0][0].rects).toEqual([[10, 20, 20, 60]])
    view.rerender(<Harness />)
    await waitFor(() => expect(update).toHaveBeenCalledTimes(2))
    expect(update.mock.calls[1][0].rects).toContainEqual([50, 0, 50, 40])
    view.rerender(<Harness speech={false} />)
    await waitFor(() => expect(update).toHaveBeenCalledTimes(3))
    expect(update.mock.calls[2][0].rects).toEqual([[10, 20, 20, 60]])
    view.rerender(<Harness speech={false} menu />)
    await waitFor(() => expect(update).toHaveBeenCalledTimes(4))
    expect(update.mock.calls[3][0].rects).toEqual([[10, 20, 20, 60], [0, 0, 100, 100]])
  })
  it('combines silhouette and bubble, deduplicates unchanged renders and adds/removes the menu', async () => {
    const view = render(<Harness />)
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1))
    expect(update.mock.calls[0][0].rects).toEqual([[10, 20, 20, 60], [50, 0, 50, 40]])
    view.rerender(<Harness />)
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
    expect(update).toHaveBeenCalledTimes(1)
    view.rerender(<Harness menu />)
    await waitFor(() => expect(update).toHaveBeenCalledTimes(2))
    expect(update.mock.calls[1][0].rects).toContainEqual([0, 0, 100, 100])
    view.rerender(<Harness />)
    await waitFor(() => expect(update).toHaveBeenCalledTimes(3))
    expect(update.mock.calls[2][0].rects).toHaveLength(2)
  })
  it('does not send a silhouette from a stale viewport while resize is pending', async () => {
    const view = render(<Harness artWidth={200} />)
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
    expect(update).not.toHaveBeenCalled()
    view.rerender(<Harness />)
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1))
  })
  it('serializes bridge updates and retains the latest layout while one is pending', async () => {
    let finish: ((value: boolean) => void) | undefined
    update.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const view = render(<Harness />)
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1))
    view.rerender(<Harness menu />)
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
    expect(update).toHaveBeenCalledTimes(1)
    await act(async () => { finish?.(true) })
    await waitFor(() => expect(update).toHaveBeenCalledTimes(2))
    expect(update.mock.calls[1][0].rects).toContainEqual([0, 0, 100, 100])
  })
})
