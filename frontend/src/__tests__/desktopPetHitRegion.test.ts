import { describe, expect, it, vi } from 'vitest'
import { alphaHitRects, clipHitRect, imageHitRegion, type HitRect } from '../features/desktopPet/hitRegion'

const contains = (rects: HitRect[], x: number, y: number) => rects.some(([left, top, w, h]) =>
  x >= left && x < left + w && y >= top && y < top + h)

describe('desktop pet silhouette input region', () => {
  it('uses the registered logical art rectangle rather than the raw PNG dimensions', () => {
    const draw = vi.fn()
    const spy = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      setTransform: vi.fn(), drawImage: draw,
      getImageData: () => ({ data: new Uint8ClampedArray(100 * 100 * 4) }),
    } as unknown as CanvasRenderingContext2D)
    try {
      const image = new Image()
      const rectangle: HitRect = [-26, 9, 422.4, 633.6]
      imageHitRegion(image, 100, 100, { x: 1, y: 2, scaleX: 0.3, scaleY: 0.3 }, rectangle)
      expect(draw).toHaveBeenCalledWith(image, ...rectangle)
    } finally { spy.mockRestore() }
  })
  it('keeps disjoint visible parts clickable while the space between passes through', () => {
    const width = 48, height = 40
    const pixels = new Uint8ClampedArray(width * height * 4)
    for (let y = 8; y < 32; y++) for (const x of [8, 9, 10, 32, 33, 34]) pixels[(y * width + x) * 4 + 3] = 255
    const rects = alphaHitRects(pixels, width, height, 0)
    expect(rects).toHaveLength(2)
    expect(contains(rects, 9, 10)).toBe(true)
    expect(contains(rects, 33, 10)).toBe(true)
    expect(contains(rects, 22, 10)).toBe(false)
    expect(contains(rects, 9, 0)).toBe(false)
    const grab = alphaHitRects(pixels, width, height)
    expect(contains(grab, 5, 10)).toBe(true)
    expect(contains(grab, 22, 10)).toBe(false)
  })
  it.each([23, 24, 25, 256])('includes the last pixel at width %s and bounds padded regions', width => {
    const height = 27, pixels = new Uint8ClampedArray(width * height * 4)
    pixels[(width * height - 1) * 4 + 3] = 255
    const rects = alphaHitRects(pixels, width, height)
    expect(contains(rects, width - 1, height - 1)).toBe(true)
    for (const [x, y, w, h] of rects) {
      expect(x).toBeGreaterThanOrEqual(0); expect(y).toBeGreaterThanOrEqual(0)
      expect(x + w).toBeLessThanOrEqual(width); expect(y + h).toBeLessThanOrEqual(height)
    }
  })
  it('does not manufacture input over transparent pixels or offscreen rectangles', () => {
    expect(alphaHitRects(new Uint8ClampedArray(40 * 40 * 4), 40, 40)).toEqual([])
    expect(clipHitRect([-10, -5, 18, 12], 40, 40)).toEqual([0, 0, 8, 7])
    expect(clipHitRect([40, 0, 20, 10], 40, 40)).toBeNull()
  })
})
