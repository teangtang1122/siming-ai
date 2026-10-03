import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { DesktopPetAmbientMode, DesktopPetBubblePlacement, DesktopPetSide } from '../behavior'
import type { DesktopPetVisualState } from '../state'
import { loadPoseImages, manifest, poseArtRect, poseById, selectPose } from './contract'
import { PoseClock } from './motion'
import { posePlacement, type PeekLayout } from './placement'
import { PoseRenderer } from './renderer'
import { imageHitRegion, type PetHitRegion } from '../hitRegion'

export interface PoseCanvasHandle {
  lookAt(x: number, y: number): void
  resetLook(): void
  tap(): void
}
export type PetLoadStatus = 'loading' | 'ready' | 'error'
interface Props {
  state: DesktopPetVisualState
  ambient: DesktopPetAmbientMode
  bubbleSide: DesktopPetSide
  bubblePlacement: DesktopPetBubblePlacement
  dragging: boolean
  peekLayout?: PeekLayout | null
  onStatusChange?: (status: PetLoadStatus) => void
  onHitRegionChange?: (region: PetHitRegion) => void
}

export const PoseCanvas = forwardRef<PoseCanvasHandle, Props>(function PoseCanvas(props, ref) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const latest = useRef(props)
  latest.current = props
  const interaction = useRef({ x: 0, y: 0, tapAt: -Infinity })
  const [status, setStatus] = useState<PetLoadStatus>('loading')
  const [error, setError] = useState('')
  const selection = selectPose(props.state, props.ambient, props.dragging)
  const { onStatusChange } = props

  useImperativeHandle(ref, () => ({
    lookAt: (x, y) => {
      interaction.current.x = Number.isFinite(x) ? Math.max(-1, Math.min(1, x)) : 0
      interaction.current.y = Number.isFinite(y) ? Math.max(-1, Math.min(1, y)) : 0
    },
    resetLook: () => { interaction.current.x = 0; interaction.current.y = 0 },
    tap: () => { interaction.current.tapAt = performance.now() },
  }), [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const abort = new AbortController()
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')
    const clock = new PoseClock()
    let renderer: PoseRenderer | undefined
    let raf = 0, last = 0, lookX = 0, lookY = 0
    let width = 1, height = 1, dpr = 1
    let resizePending = true
    let images: Map<string, HTMLImageElement>, hitSignature = ''
    const report = (next: PetLoadStatus) => { setStatus(next); onStatusChange?.(next) }
    const fail = (reason: unknown) => {
      if (abort.signal.aborted) return
      cancelAnimationFrame(raf)
      abort.abort()
      renderer?.dispose()
      setError(reason instanceof Error ? reason.message : '桌宠姿势加载失败')
      report('error')
    }
    const resize = () => { resizePending = true }
    const measure = () => {
      if (!resizePending) return false
      resizePending = false
      const rect = canvas.getBoundingClientRect()
      const nextWidth = Math.max(1, rect.width), nextHeight = Math.max(1, rect.height)
      const nextDpr = Math.min(2, window.devicePixelRatio || 1)
      if (width === nextWidth && height === nextHeight && dpr === nextDpr
        && canvas.width === Math.round(nextWidth * nextDpr) && canvas.height === Math.round(nextHeight * nextDpr)) return false
      width = Math.max(1, rect.width)
      height = Math.max(1, rect.height)
      dpr = nextDpr
      // Assigning even an unchanged dimension erases the bitmap. Coalesce both
      // resize sources and reset only immediately before painting the new size.
      if (canvas.width !== Math.round(width * dpr)) canvas.width = Math.round(width * dpr)
      if (canvas.height !== Math.round(height * dpr)) canvas.height = Math.round(height * dpr)
      return true
    }
    const draw = (now: number) => {
      const resized = measure()
      const current = latest.current
      const selected = selectPose(current.state, current.ambient, current.dragging)
      const changed = selected.id !== clock.current
      clock.select(selected.id)
      const interval = selected.id === 'dozing' ? 50 : 1000 / 30
      if (last && now - last < interval && !changed && !resized) return
      const delta = last ? now - last : 0
      last = now
      const motion = clock.advance(delta, !reduced.matches)
      // Small whole-mesh response only: no dislocated hands or pupil overlays.
      const response = 1 - Math.exp(-Math.min(delta || 16, 100) / 130)
      lookX += (interaction.current.x - lookX) * response
      lookY += (interaction.current.y - lookY) * response
      if (!reduced.matches && selected.id !== 'dozing' && !current.dragging) {
        motion.lean += lookX * 2
        motion.nod += lookY * 1.2
        const tapped = (now - interaction.current.tapAt) / 520
        if (tapped >= 0 && tapped < 1) motion.nod += Math.sin(tapped * Math.PI) * 9
      }
      const pose = poseById[selected.id]
      const source = renderer!.draw(pose, motion)
      const placement = posePlacement(pose, width, height, current.bubbleSide, current.bubblePlacement, selected.edge, current.peekLayout)
      const nextHitSignature = JSON.stringify([pose.id, width, height, placement])
      if (current.onHitRegionChange && nextHitSignature !== hitSignature) {
        const art = images.get(pose.files[0])
        if (!art) throw new Error(`点击轮廓缺少素材：${pose.id}`)
        current.onHitRegionChange(imageHitRegion(art, width, height, placement, poseArtRect(pose)))
        hitSignature = nextHitSignature
      }
      const ctx = canvas.getContext('2d', { alpha: true })
      if (!ctx) throw new Error('无法显示桌宠画布。')
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      ctx.setTransform(dpr * placement.scaleX, 0, 0, dpr * placement.scaleY,
        dpr * placement.x, dpr * (placement.y - (current.dragging && !reduced.matches ? 4 : 0)))
      ctx.drawImage(source, 0, 0)
      canvas.dataset.eyeFrame = String(motion.frame)
    }
    const tick = (now: number) => {
      if (abort.signal.aborted) return
      try { draw(now) } catch (reason) { fail(reason); return }
      raf = requestAnimationFrame(tick)
    }
    const visibility = () => {
      cancelAnimationFrame(raf)
      last = 0
      if (!document.hidden && renderer && !abort.signal.aborted) raf = requestAnimationFrame(tick)
    }
    const observer = new ResizeObserver(resize)
    observer.observe(canvas)
    window.addEventListener('resize', resize)
    document.addEventListener('visibilitychange', visibility)
    report('loading')
    void loadPoseImages(abort.signal).then(loaded => {
      if (abort.signal.aborted) return
      images = loaded
      renderer = new PoseRenderer(images)
      resize()
      draw(performance.now())
      report('ready')
      if (!document.hidden) raf = requestAnimationFrame(tick)
    }).catch(fail)
    return () => {
      abort.abort()
      cancelAnimationFrame(raf)
      observer.disconnect()
      window.removeEventListener('resize', resize)
      document.removeEventListener('visibilitychange', visibility)
      renderer?.dispose()
    }
  }, [onStatusChange])

  return <>
    <canvas ref={canvasRef} className="desktop-pet__pose-canvas" aria-hidden="true"
      data-pose={selection.id} data-edge={selection.edge || ''} data-pose-version={manifest.version} />
    {status !== 'ready' && <span className={`desktop-pet__model-message desktop-pet__model-message--${status}`} title={error || undefined}>
      <i aria-hidden="true" />{status === 'loading' ? '正在唤醒司命' : '司命素材加载失败'}
    </span>}
  </>
})
