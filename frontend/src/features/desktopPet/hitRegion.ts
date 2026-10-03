export type HitRect = [x: number, y: number, width: number, height: number]
export interface PetHitRegion { width: number; height: number; rects: HitRect[] }
export interface NativeHitRegion { viewport_width: number; viewport_height: number; rects: HitRect[] }

export function clipHitRect(rect: HitRect, width: number, height: number, padding = 0): HitRect | null {
  const left = Math.max(0, Math.floor(rect[0] - padding)), top = Math.max(0, Math.floor(rect[1] - padding))
  const right = Math.min(width, Math.ceil(rect[0] + rect[2] + padding))
  const bottom = Math.min(height, Math.ceil(rect[1] + rect[3] + padding))
  return right > left && bottom > top ? [left, top, right - left, bottom - top] : null
}

// Scan once on a pose/layout change, not every animation frame. Four-pixel
// bands preserve the silhouette and holes, with a small grab/motion margin.
export function alphaHitRects(pixels: Uint8ClampedArray, width: number, height: number,
  padding = 6, step = 4): HitRect[] {
  const rects: HitRect[] = []
  let previous = new Map<string, HitRect>()
  for (let y = 0; y < height; y += step) {
    const current = new Map<string, HitRect>()
    let run = -1
    for (let x = 0; x <= width; x += step) {
      let opaque = false
      if (x < width) {
        for (let py = y; py < Math.min(y + step, height) && !opaque; py++) {
          for (let px = x; px < Math.min(x + step, width); px++) {
            if (pixels[(py * width + px) * 4 + 3] >= 16) { opaque = true; break }
          }
        }
      }
      if (opaque && run < 0) run = x
      if (run >= 0 && (!opaque || x + step >= width)) {
        const right = opaque ? width : x
        const key = `${run}:${right}`
        const last = previous.get(key)
        const band: HitRect = last || [run, y, right - run, 0]
        band[3] += Math.min(step, height - y)
        if (!last) rects.push(band)
        current.set(key, band)
        run = -1
      }
    }
    previous = current
  }
  return rects.map(rect => clipHitRect(rect, width, height, padding)).filter((rect): rect is HitRect => Boolean(rect))
}

export function imageHitRegion(image: HTMLImageElement, width: number, height: number,
  transform: { x: number; y: number; scaleX: number; scaleY: number },
  artRect: HitRect): PetHitRegion {
  const mask = document.createElement('canvas')
  mask.width = Math.ceil(width); mask.height = Math.ceil(height)
  const ctx = mask.getContext('2d', { willReadFrequently: true })
  if (!ctx) throw new Error('无法计算司命的点击轮廓。')
  ctx.setTransform(transform.scaleX, 0, 0, transform.scaleY, transform.x, transform.y)
  ctx.drawImage(image, ...artRect)
  const pixels = ctx.getImageData(0, 0, mask.width, mask.height)
  return { width, height, rects: alphaHitRects(pixels.data, mask.width, mask.height)
    .map(rect => clipHitRect(rect, width, height)).filter((rect): rect is HitRect => Boolean(rect)) }
}
