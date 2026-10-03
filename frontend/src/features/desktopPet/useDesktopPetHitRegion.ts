import { useCallback, useEffect, useLayoutEffect, useRef, type RefObject } from 'react'
import { clipHitRect, type NativeHitRegion, type PetHitRegion } from './hitRegion'
import { hasDesktopPetBridge, updateDesktopPetHitRegion } from './nativeBridge'

export function useDesktopPetHitRegion(rootRef: RefObject<HTMLElement>, onError: (message: string) => void) {
  const character = useRef<PetHitRegion | null>(null)
  const scheduleRef = useRef<() => void>(() => {})
  const errorRef = useRef(onError)
  errorRef.current = onError
  useLayoutEffect(() => { scheduleRef.current() })
  useEffect(() => {
    const root = rootRef.current
    if (!root) return
    let cancelled = false, timer = 0, busy = false, again = false, failures = 0
    let applied = ''
    const schedule = () => {
      window.clearTimeout(timer)
      if (!cancelled) timer = window.setTimeout(() => { void flush() }, 0)
    }
    const flush = async () => {
      if (cancelled || !hasDesktopPetBridge()) return
      if (busy) { again = true; return }
      const bounds = root.getBoundingClientRect()
      const { width, height } = bounds
      if (!width || !height) return
      // A resize may precede the next canvas render; don't install an old
      // silhouette over a new viewport. The canvas callback schedules us again.
      const art = character.current
      if (art && (Math.abs(art.width - width) > 0.5 || Math.abs(art.height - height) > 0.5)) return
      const rects = [...(art?.rects || [])]
      root.querySelectorAll('.desktop-pet__speech, .desktop-pet__menu, .desktop-pet__model-message').forEach(element => {
        const rect = element.getBoundingClientRect()
        const hit = clipHitRect([rect.x - bounds.x, rect.y - bounds.y, rect.width, rect.height], width, height, 10)
        if (hit) rects.push(hit)
      })
      const packet: NativeHitRegion = { viewport_width: width, viewport_height: height, rects }
      const signature = JSON.stringify(packet)
      if (signature === applied) return
      busy = true
      try {
        if (!await updateDesktopPetHitRegion(packet)) throw new Error('点击轮廓尚未就绪')
        applied = signature; failures = 0
        if (!cancelled) root.dataset.petHitRegion = 'ready'
      } catch {
        if (!cancelled && ++failures >= 4) {
          root.dataset.petHitRegion = 'error'
          errorRef.current('透明点击区域同步失败，请重新打开桌宠。')
        }
      } finally {
        busy = false
        if (!cancelled && failures > 0 && failures < 4) timer = window.setTimeout(() => { void flush() }, 350)
        else if (again && !cancelled) { again = false; schedule() }
      }
    }
    scheduleRef.current = schedule
    const resize = new ResizeObserver(schedule)
    resize.observe(root)
    const mutation = new MutationObserver(schedule)
    mutation.observe(root, { childList: true, subtree: true, characterData: true })
    window.addEventListener('pywebviewready', schedule)
    root.addEventListener('animationend', schedule)
    schedule()
    return () => {
      cancelled = true; scheduleRef.current = () => {}
      window.clearTimeout(timer); resize.disconnect(); mutation.disconnect()
      window.removeEventListener('pywebviewready', schedule)
      root.removeEventListener('animationend', schedule)
    }
  }, [rootRef])
  return useCallback((region: PetHitRegion) => {
    character.current = region
    scheduleRef.current()
  }, [])
}
