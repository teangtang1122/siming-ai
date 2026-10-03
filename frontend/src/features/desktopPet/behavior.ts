import type { DesktopPetPresentation } from './state'

export type DesktopPetSide = 'left' | 'right'
export type DesktopPetBubblePlacement = 'side' | 'top'

export type DesktopPetAmbientMode = 'awake' | 'sleeping' | 'reading'
  | 'going-left' | 'going-right' | 'peek-left' | 'peek-right'

export interface DesktopPetWindowContext {
  x: number
  y: number
  width: number
  height: number
  work_x: number
  work_y: number
  work_width: number
  work_height: number
  compact?: boolean
}

export interface DesktopPetResizeAnchor {
  x: 'left' | 'right'
  y: 'top' | 'bottom'
}

// Choose once at gesture start. A larger window must have room to grow on
// that side, otherwise clamping changes anchors midway through the drag.
export function desktopPetResizeAnchor(context: DesktopPetWindowContext): DesktopPetResizeAnchor {
  return {
    x: context.x + context.width - 459 >= context.work_x ? 'right' : 'left',
    y: context.y + context.height - 405 >= context.work_y ? 'bottom' : 'top',
  }
}

export const DESKTOP_PET_BUBBLE_SIDE_MIN_VISIBLE_PX = 230

export function desktopPetEdgeSide(context: DesktopPetWindowContext): DesktopPetSide | null {
  // Native coordinates can be device pixels: scale the magnet with the window.
  const threshold = Math.max(12, context.width * 0.08)
  const left = Math.abs(context.x - context.work_x)
  const right = Math.abs(context.work_x + context.work_width - context.width - context.x)
  if (Math.min(left, right) > threshold) return null
  return left <= right ? 'left' : 'right'
}

export function desktopPetIdleChoice(random = Math.random()): 'sleeping' | 'peek-left' | 'peek-right' {
  return random < 0.5 ? 'sleeping' : random < 0.75 ? 'peek-left' : 'peek-right'
}

export function desktopPetPeekSide(mode: DesktopPetAmbientMode): DesktopPetSide | null {
  return mode === 'peek-left' ? 'left' : mode === 'peek-right' ? 'right' : null
}

export function desktopPetBubbleSide(
  context: DesktopPetWindowContext,
): DesktopPetSide {
  const petCenter = context.x + context.width / 2
  const workCenter = context.work_x + context.work_width / 2
  return petCenter <= workCenter ? 'right' : 'left'
}

export function desktopPetBubblePlacement(
  context: DesktopPetWindowContext,
  minimumVisibleWidth = DESKTOP_PET_BUBBLE_SIDE_MIN_VISIBLE_PX,
): DesktopPetBubblePlacement {
  const workRight = context.work_x + context.work_width
  const windowRight = context.x + context.width
  const visibleWidth = Math.max(
    0,
    Math.min(windowRight, workRight) - Math.max(context.x, context.work_x),
  )
  return visibleWidth < minimumVisibleWidth ? 'top' : 'side'
}

export function applyDesktopPetAmbientPresentation(
  presentation: DesktopPetPresentation,
  ambientMode: DesktopPetAmbientMode,
): DesktopPetPresentation {
  if (presentation.operation) return presentation
  if (desktopPetPeekSide(ambientMode)) {
    return {
      ...presentation,
      eyebrow: '探出半个小脑袋',
      title: '偷偷看你～',
      detail: '我躲好啦……你有发现我吗？|･ω･｀)',
      actionLabel: '叫我出来',
    }
  }
  if (ambientMode === 'going-left' || ambientMode === 'going-right') {
    return { ...presentation, eyebrow: '小步挪挪', title: '去旁边看看～' }
  }
  if (ambientMode === 'reading') {
    return { ...presentation, eyebrow: '翻翻小书', title: '这一页真有趣～',
      detail: '陪你读一小会儿书，看到有趣的就告诉你 (｡･ω･｡)ﾉ♡' }
  }
  if (ambientMode !== 'sleeping') {
    return presentation
  }
  return {
    ...presentation,
    state: 'sleeping',
    eyebrow: '眯一小会儿',
    title: '呼……轻轻的',
    detail: '我就这样陪着你，困了眯一小会儿……(_ _).｡o○',
    actionLabel: '戳醒我',
  }
}
