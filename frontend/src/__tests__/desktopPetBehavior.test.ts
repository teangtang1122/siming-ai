import { describe, expect, it } from 'vitest'
import {
  applyDesktopPetAmbientPresentation,
  desktopPetBubblePlacement,
  desktopPetBubbleSide,
  desktopPetEdgeSide,
  desktopPetIdleChoice,
  desktopPetResizeAnchor,
  type DesktopPetWindowContext,
} from '../features/desktopPet/behavior'
import { deriveDesktopPetPresentation } from '../features/desktopPet/state'

const leftContext: DesktopPetWindowContext = {
  x: 0,
  y: 200,
  width: 272,
  height: 240,
  work_x: 0,
  work_y: 0,
  work_width: 1920,
  work_height: 1040,
}

describe('desktop pet ambient behavior', () => {
  it('chooses a scale anchor with room for maximum growth, including negative monitors', () => {
    expect(desktopPetResizeAnchor(leftContext)).toEqual({ x: 'left', y: 'bottom' })
    expect(desktopPetResizeAnchor({ ...leftContext, x: 600, y: 0 })).toEqual({ x: 'right', y: 'top' })
    expect(desktopPetResizeAnchor({ ...leftContext, x: -1900, y: -480, work_x: -1920, work_y: -500 }))
      .toEqual({ x: 'left', y: 'top' })
    expect(desktopPetResizeAnchor({ ...leftContext, x: -272, y: 500, work_x: -1920 }))
      .toEqual({ x: 'right', y: 'bottom' })
  })
  it('keeps dozing and peeking separate from active task presentation', () => {
    const idle = deriveDesktopPetPresentation([])
    expect(applyDesktopPetAmbientPresentation(idle, 'sleeping')).toMatchObject({
      state: 'sleeping',
      eyebrow: '眯一小会儿',
    })
    expect(applyDesktopPetAmbientPresentation(idle, 'awake')).toBe(idle)
    const active = { ...idle, operation: { id: 'active' } } as typeof idle
    expect(applyDesktopPetAmbientPresentation(active, 'sleeping')).toBe(active)
    expect(applyDesktopPetAmbientPresentation(active, 'peek-left')).toBe(active)
    expect(applyDesktopPetAmbientPresentation(idle, 'peek-right').title).toBe('偷偷看你～')
  })

  it('detects a released window close to either work-area edge, including negative coordinates', () => {
    expect(desktopPetEdgeSide(leftContext)).toBe('left')
    expect(desktopPetEdgeSide({ ...leftContext, x: 1648 })).toBe('right')
    expect(desktopPetEdgeSide({ ...leftContext, x: 1629 })).toBe('right')
    expect(desktopPetEdgeSide({ ...leftContext, x: 600 })).toBeNull()
    expect(desktopPetEdgeSide({ ...leftContext, x: -1920, work_x: -1920 })).toBe('left')
    expect(desktopPetEdgeSide({ ...leftContext, x: -272, work_x: -1920 })).toBe('right')
  })

  it('chooses sleep half of the time and splits peeking across both sides', () => {
    expect(desktopPetIdleChoice(0)).toBe('sleeping')
    expect(desktopPetIdleChoice(0.499)).toBe('sleeping')
    expect(desktopPetIdleChoice(0.5)).toBe('peek-left')
    expect(desktopPetIdleChoice(0.749)).toBe('peek-left')
    expect(desktopPetIdleChoice(0.75)).toBe('peek-right')
    expect(desktopPetIdleChoice(0.999)).toBe('peek-right')
  })

  it('keeps the bubble toward free space at both edges and on negative-coordinate displays', () => {
    expect(desktopPetBubbleSide(leftContext)).toBe('right')
    expect(desktopPetBubbleSide({ ...leftContext, x: 1648 })).toBe('left')
    expect(desktopPetBubbleSide({ ...leftContext, x: -1920, work_x: -1920 })).toBe('right')
    expect(desktopPetBubbleSide({ ...leftContext, x: -272, work_x: -1920 })).toBe('left')
  })

  it('moves the bubble above the head only when the visible edge area is too narrow', () => {
    expect(desktopPetBubblePlacement(leftContext)).toBe('side')
    expect(desktopPetBubblePlacement({ ...leftContext, x: -49 })).toBe('top')
    expect(desktopPetBubblePlacement({ ...leftContext, x: 600 })).toBe('side')
  })
})
