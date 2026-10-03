import { useEffect, useRef } from 'react'
import type { DesktopPetResizeAnchor } from './behavior'

interface Props {
  label: string
  value: number
  min: number
  max: number
  step: number
  getResizeAnchor?(): DesktopPetResizeAnchor
  onChange(value: number, anchor: DesktopPetResizeAnchor | null): void
  onCommit(value: number, anchor: DesktopPetResizeAnchor | null): void
}

// The transparent native window moves/resizes underneath this range. Browser
// default range dragging can lose its target then; own the gesture in fixed
// screen coordinates, while keeping native keyboard/accessibility semantics.
export function PetSettingsSlider({ label, value, min, max, step, onChange, onCommit, getResizeAnchor }: Props) {
  const gesture = useRef<{ pointerId: number; screenX: number; value: number; trackWidth: number; anchor: DesktopPetResizeAnchor | null } | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const frozenMenu = useRef<{ element: HTMLElement; style: Partial<CSSStyleDeclaration> } | null>(null)
  const latest = useRef(value)
  latest.current = value
  const snap = (next: number) => Math.max(min, Math.min(max, Number((min + Math.round((next - min) / step) * step).toFixed(4))))
  const update = (next: number) => { latest.current = next; onChange(next, gesture.current?.anchor ?? null) }
  const move = (event: { pointerId: number; screenX: number; buttons: number }) => {
    const drag = gesture.current
    if (!drag || drag.pointerId !== event.pointerId || !(event.buttons & 1)) return
    update(snap(drag.value + (event.screenX - drag.screenX) / drag.trackWidth * (max - min)))
  }
  const finish = (event: { pointerId: number }) => {
    if (gesture.current?.pointerId !== event.pointerId) return
    const anchor = gesture.current.anchor
    gesture.current = null
    if (input.current?.hasPointerCapture(event.pointerId)) input.current.releasePointerCapture(event.pointerId)
    const menu = frozenMenu.current
    if (menu) {
      Object.assign(menu.element.style, menu.style)
      frozenMenu.current = null
    }
    onCommit(latest.current, anchor)
  }
  const handlers = useRef({ move, finish })
  handlers.current = { move, finish }
  useEffect(() => {
    // WebView2 drops DOM pointer capture on a native SetBounds. WinForms still
    // forwards the captured OS drag, but the DOM target may now be elsewhere.
    // Listen for this one active gesture at the window, not at the moving input.
    const onMove = (event: globalThis.PointerEvent) => handlers.current.move(event)
    const onEnd = (event: globalThis.PointerEvent) => {
      if (event.type === 'pointerup') handlers.current.move({ pointerId: event.pointerId, screenX: event.screenX, buttons: 1 })
      handlers.current.finish(event)
    }
    const onBlur = () => { if (gesture.current) handlers.current.finish(gesture.current) }
    window.addEventListener('pointermove', onMove, true)
    window.addEventListener('pointerup', onEnd, true)
    window.addEventListener('pointercancel', onEnd, true)
    window.addEventListener('blur', onBlur)
    return () => {
      onBlur()
      window.removeEventListener('pointermove', onMove, true)
      window.removeEventListener('pointerup', onEnd, true)
      window.removeEventListener('pointercancel', onEnd, true)
      window.removeEventListener('blur', onBlur)
    }
  }, [])
  return <input ref={input} aria-label={label} type="range" min={min} max={max} step={step} value={value}
    onPointerDown={event => {
      if (event.button !== 0 || gesture.current) return
      event.preventDefault()
      event.currentTarget.focus({ preventScroll: true })
      event.currentTarget.setPointerCapture(event.pointerId)
      const anchor = getResizeAnchor?.() ?? null
      const menu = event.currentTarget.closest<HTMLElement>('.desktop-pet__menu')
      if (menu) {
        const rect = menu.getBoundingClientRect()
        frozenMenu.current = { element: menu, style: {
          width: menu.style.width, height: menu.style.height, maxHeight: menu.style.maxHeight,
          left: menu.style.left, top: menu.style.top, right: menu.style.right, bottom: menu.style.bottom,
        } }
        menu.style.width = `${rect.width}px`
        menu.style.height = `${rect.height}px`
        menu.style.maxHeight = 'none'
        if (anchor?.x === 'left') { menu.style.left = `${rect.left}px`; menu.style.right = 'auto' }
        if (anchor?.y === 'top') { menu.style.top = `${rect.top}px`; menu.style.bottom = 'auto' }
      }
      const rect = event.currentTarget.getBoundingClientRect()
      // Chromium's range thumb is 16 CSS px in this control. Keeping the start
      // width fixed also prevents a growing menu from changing pointer gain.
      const trackWidth = Math.max(1, rect.width - 16)
      const next = snap(min + (event.clientX - rect.left - 8) / trackWidth * (max - min))
      gesture.current = { pointerId: event.pointerId, screenX: event.screenX, value: next, trackWidth, anchor }
      update(next)
    }}
    onChange={event => { if (!gesture.current) update(Number(event.currentTarget.value)) }}
    onKeyUp={() => onCommit(latest.current, null)}
    onBlur={() => { if (!gesture.current) onCommit(latest.current, null) }}
  />
}
