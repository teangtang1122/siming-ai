import type { DesktopPetBubblePlacement, DesktopPetSide } from '../behavior'
import { manifest, poseById, type Pose } from './contract'

export const PEEK_BUBBLE_MAX_HEIGHT = 88

export interface PeekLayout {
  normalWidth: number
  normalHeight: number
  width: number
  height: number
  bubbleWidth: number
  bubbleTop: number
  bubbleInset: number
}

export function compactPeekLayout(width: number, height: number): PeekLayout {
  const pose = poseById.peeking
  const full = posePlacement(pose, width, height, 'right', 'side', 'left')
  // Include the hair tips and the maximum subtle lean, not just the face.
  const strip = pose.edge!.visibleStripBounds
  const headRight = full.x + (strip[2] + 5) * full.scaleX
  const headBottom = full.y + (strip[3] + 4) * full.scaleY
  const bubbleWidth = Math.min(150, width * 0.48)
  const bubbleInset = Math.ceil(headRight + 14)
  const bubbleTop = Math.round(height * 0.3)
  return {
    normalWidth: width, normalHeight: height, bubbleWidth, bubbleInset, bubbleTop,
    width: Math.min(width, Math.ceil(bubbleInset + bubbleWidth + 12)),
    // Fixed reserve matches the peek bubble's max-height. Changing chatter
    // cannot shrink/grow the native window under the pointer.
    height: Math.min(height, Math.ceil(Math.max(headBottom, bubbleTop + PEEK_BUBBLE_MAX_HEIGHT) + 12)),
  }
}

export function posePlacement(pose: Pose, width: number, height: number,
  bubbleSide: DesktopPetSide, bubblePlacement: DesktopPetBubblePlacement, edge: DesktopPetSide | null,
  reference?: Pick<PeekLayout, 'normalWidth' | 'normalHeight'> | null) {
  if (![width, height].every(value => Number.isFinite(value) && value > 0)) throw new RangeError('Invalid viewport')
  // One head scale across poses, including the seated pose; never fit each art's
  // own bounding box (that made clicks and pose changes inflate the character).
  const topBubble = bubblePlacement === 'top'
  const normalWidth = reference?.normalWidth ?? width
  const normalHeight = reference?.normalHeight ?? height
  const availableHeight = topBubble ? Math.max(40, normalHeight - 122) : normalHeight * 0.84
  const availableWidth = topBubble ? normalWidth - 12 : normalWidth * 0.49
  const scale = Math.min(availableHeight / 489, availableWidth / 344)
  if (edge) {
    if (!pose.edge) throw new Error('Peeking artwork requires screen-edge anchors')
    const [, top, , bottom] = pose.edge.visibleStripBounds
    return {
      x: edge === 'left' ? -pose.edge.boundaryX * scale : width + pose.edge.boundaryX * scale,
      y: (topBubble ? 122 : 0) + availableHeight / 2 - (top + bottom) / 2 * scale,
      scaleX: edge === 'left' ? scale : -scale,
      scaleY: scale,
    }
  }
  const center = topBubble ? width / 2 : width * (bubbleSide === 'right' ? 0.25 : 0.75)
  return { x: center - manifest.width / 2 * scale,
    y: height * 0.94 - manifest.floor * scale, scaleX: scale, scaleY: scale }
}
