import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PetSettingsSlider } from '../features/desktopPet/PetSettingsSlider'
import type { DesktopPetResizeAnchor } from '../features/desktopPet/behavior'

let bounds = { left: 100, width: 146, top: 50, height: 16 }
const commit = vi.fn(), change = vi.fn()
function Slider({ anchor }: { anchor?: DesktopPetResizeAnchor }) {
  const [value, setValue] = useState(0.7)
  return <section className="desktop-pet__menu" style={{ maxHeight: '90%' }}>
    <PetSettingsSlider label="大小" value={value} min={0.7} max={1.35} step={0.01}
      getResizeAnchor={anchor ? () => anchor : undefined}
      onChange={next => { setValue(next); change(next) }} onCommit={commit} />
  </section>
}
function down() {
  fireEvent.pointerDown(screen.getByRole('slider'), { button: 0, buttons: 1, pointerId: 9, clientX: 108, screenX: 500 })
}
beforeEach(() => {
  vi.clearAllMocks()
  bounds = { left: 100, width: 146, top: 50, height: 16 }
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    return (this.tagName === 'INPUT' ? bounds : { left: 0, top: 0, width: 240, height: 200 }) as DOMRect
  })
  Object.defineProperties(HTMLInputElement.prototype, {
    setPointerCapture: { configurable: true, value: vi.fn() },
    hasPointerCapture: { configurable: true, value: vi.fn(() => false) },
  })
})
afterEach(() => {
  vi.restoreAllMocks()
  Reflect.deleteProperty(HTMLInputElement.prototype, 'setPointerCapture')
  Reflect.deleteProperty(HTMLInputElement.prototype, 'hasPointerCapture')
})

describe('desktop range gesture through a moving WebView', () => {
  it('pins the menu at a monitor corner and retains the starting native anchor', () => {
    const view = render(<Slider anchor={{ x: 'left', y: 'top' }} />)
    const menu = screen.getByRole('slider').closest('section')!
    down()
    expect(menu).toHaveStyle({ left: '0px', top: '0px', right: 'auto', bottom: 'auto' })
    view.rerender(<Slider anchor={{ x: 'right', y: 'bottom' }} />)
    fireEvent.pointerMove(window, { pointerId: 9, buttons: 1, screenX: 560 })
    fireEvent.pointerUp(window, { pointerId: 9, screenX: 560 })
    expect(commit).toHaveBeenCalledExactlyOnceWith(1, { x: 'left', y: 'top' })
    expect(menu.style.left).toBe('')
    expect(menu.style.top).toBe('')
  })
  it('keeps dragging after native resize loses capture and changes the event target', () => {
    render(<Slider />)
    const slider = screen.getByRole('slider'), menu = slider.closest('section')!
    down()
    expect(menu).toHaveStyle({ width: '240px', height: '200px', maxHeight: 'none' })
    bounds = { left: 5, width: 250, top: 5, height: 16 }
    fireEvent.lostPointerCapture(slider, { pointerId: 9, buttons: 1 })
    fireEvent.pointerMove(document.body, { pointerId: 9, buttons: 1, screenX: 550 })
    expect(slider).toHaveValue('0.95')
    fireEvent.pointerMove(window, { pointerId: 9, buttons: 1, screenX: 630 })
    expect(slider).toHaveValue('1.35')
    expect(commit).not.toHaveBeenCalled()
    fireEvent.pointerUp(document.body, { pointerId: 9, screenX: 630 })
    fireEvent.lostPointerCapture(slider, { pointerId: 9 })
    expect(commit).toHaveBeenCalledExactlyOnceWith(1.35, null)
    expect(menu.style.width).toBe('')
    expect(menu.style.height).toBe('')
    expect(menu.style.maxHeight).toBe('90%')
  })
  it('uses fixed screen-space gain in both directions, clamps and ignores hover/other pointers', () => {
    render(<Slider />)
    const slider = screen.getByRole('slider')
    down()
    fireEvent.pointerMove(window, { pointerId: 8, buttons: 1, screenX: 900 })
    fireEvent.pointerMove(window, { pointerId: 9, buttons: 0, screenX: 900 })
    expect(slider).toHaveValue('0.7')
    fireEvent.pointerMove(window, { pointerId: 9, buttons: 1, screenX: 800 })
    expect(slider).toHaveValue('1.35')
    fireEvent.pointerMove(window, { pointerId: 9, buttons: 1, screenX: 580 })
    expect(slider).toHaveValue('1.1')
    fireEvent.pointerMove(window, { pointerId: 9, buttons: 1, screenX: 400 })
    expect(slider).toHaveValue('0.7')
  })
  it.each(['pointercancel', 'blur'])('finishes safely on window %s and restores the menu', event => {
    render(<Slider />)
    down()
    fireEvent.pointerMove(window, { pointerId: 9, buttons: 1, screenX: 560 })
    if (event === 'blur') fireEvent.blur(window)
    else fireEvent.pointerCancel(window, { pointerId: 9 })
    expect(commit).toHaveBeenCalledExactlyOnceWith(1, null)
    expect(screen.getByRole('slider').closest('section')!.style.width).toBe('')
    fireEvent.pointerMove(window, { pointerId: 9, buttons: 1, screenX: 620 })
    expect(screen.getByRole('slider')).toHaveValue('1')
  })
  it('keeps keyboard changes, track clicks and non-left buttons distinct', () => {
    render(<Slider />)
    const slider = screen.getByRole('slider')
    fireEvent.pointerDown(slider, { button: 2, pointerId: 9, clientX: 108, screenX: 500 })
    expect(change).not.toHaveBeenCalled()
    fireEvent.change(slider, { target: { value: '0.82' } })
    fireEvent.keyUp(slider, { key: 'ArrowRight' })
    expect(commit).toHaveBeenLastCalledWith(0.82, null)
    fireEvent.pointerDown(slider, { button: 0, pointerId: 9, clientX: 208, screenX: 600 })
    fireEvent.pointerUp(window, { pointerId: 9, screenX: 600 })
    expect(commit).toHaveBeenLastCalledWith(1.2, null)
  })
  it('removes global listeners and finishes an active gesture on unmount', () => {
    const view = render(<Slider />)
    down()
    view.unmount()
    expect(commit).toHaveBeenCalledExactlyOnceWith(0.7, null)
    change.mockClear()
    fireEvent.pointerMove(window, { pointerId: 9, buttons: 1, screenX: 580 })
    expect(change).not.toHaveBeenCalled()
  })
})
