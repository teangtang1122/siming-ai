import { describe, expect, it } from 'vitest'
import { manifest, POSE_IDS, poseArtRect, poseById, selectPose } from '../features/desktopPet/poses/contract'
import { blinkFrame, deformPoint, PoseClock } from '../features/desktopPet/poses/motion'
import { compactPeekLayout, PEEK_BUBBLE_MAX_HEIGHT, posePlacement } from '../features/desktopPet/poses/placement'

describe('current desktop pet pose contract', () => {
  it('uses only the current 15 frames and whole poses', () => {
    expect(manifest.poses.map(pose => pose.id)).toEqual(POSE_IDS)
    expect(Object.keys(manifest.sha256)).toHaveLength(15)
    expect(poseById.reading.frameMode).toBe('local_eyes')
    expect(poseById.peeking.frameMode).toBe('local_eyes')
    expect(poseById.picked_up.frameMode).toBe('local_eyes')
    expect(poseById.picked_up.files).toHaveLength(3)
    expect(poseById.dozing.files).toHaveLength(1)
  })
  it('registers the untouched high-resolution pickup art in the same logical canvas', () => {
    expect(poseArtRect(poseById.standing)).toEqual([0, 0, 384, 576])
    const [x, y, width, height] = poseArtRect(poseById.picked_up)
    expect(width / 1024).toBeCloseTo(height / 1536)
    expect(width / 1024).toBeCloseTo(0.4125)
    expect(x + 528.5 * 0.4125).toBeCloseTo(192)
    expect(y + 1335 * 0.4125).toBeCloseTo(manifest.floor)
  })
  it.each(['thinking', 'writing'] as const)('%s always uses reading, overriding stale ambient input', (state) => {
    expect(selectPose(state, 'peek-left')).toEqual({ id: 'reading', edge: null })
  })
  it.each(['waiting_author', 'error', 'completed'] as const)('%s remains an awake whole pose', (state) => {
    expect(selectPose(state, 'reading')).toEqual({ id: 'standing', edge: null })
  })
  it('keeps sleep, manual reading and native-edge poses distinct', () => {
    expect(selectPose('sleeping', 'sleeping').id).toBe('dozing')
    expect(selectPose('idle', 'reading').id).toBe('reading')
    expect(selectPose('idle', 'awake').id).toBe('standing')
    expect(selectPose('idle', 'going-left').id).toBe('standing')
    expect(selectPose('idle', 'peek-left')).toEqual({ id: 'peeking', edge: 'left' })
    expect(selectPose('idle', 'peek-right')).toEqual({ id: 'peeking', edge: 'right' })
  })
  it.each(['reading', 'peeking', 'picked_up'] as const)('%s closes then reopens both eye regions', (id) => {
    expect(blinkFrame(id, -0.01)).toBe(0)
    expect(blinkFrame(id, 0.03)).toBe(1)
    expect(blinkFrame(id, 0.1)).toBe(2)
    expect(blinkFrame(id, 0.2)).toBe(1)
    expect(blinkFrame(id, 0.25)).toBe(0)
    expect(blinkFrame(id, 8.1)).toBe(0)
  })
  it.each(POSE_IDS.filter(id => id !== 'picked_up'))('%s keeps feet planted, mostly still and bounded through two minutes', (id) => {
    const pose = poseById[id]
    const clock = new PoseClock(id, () => 0.5)
    let quiet = 0, moving = 0
    for (let tick = 0; tick < 1200; tick++) {
      const motion = clock.advance(100)
      expect(pose.files[motion.frame]).toBeTruthy()
      expect(deformPoint([192, 560], pose, motion)).toEqual([192, 560])
      expect(Math.abs(motion.lean)).toBeLessThanOrEqual(3)
      expect(Math.abs(motion.nod)).toBeLessThanOrEqual(5.4)
      if (motion.lean === 0 && motion.nod === 0) quiet++
      else moving++
    }
    expect(quiet / 1200).toBeGreaterThan(0.7)
    expect(moving).toBeGreaterThan(0)
    expect(clock.advance(100, false)).toEqual({ lean: 0, nod: 0, breath: 0, frame: 0 })
  })
  it.each(['idle', 'thinking', 'writing', 'waiting_author', 'error', 'completed', 'sleeping'] as const)(
    'uses the dedicated suspension pose during a %s drag and restores the real state on release', state => {
      expect(selectPose(state, 'peek-right', true)).toEqual({ id: 'picked_up', edge: null })
      expect(selectPose(state, 'awake', false).id).not.toBe('picked_up')
    },
  )
  it('blinks while held without making sleeves or disconnected limbs oscillate', () => {
    const clock = new PoseClock('picked_up', () => 0.5)
    const frames = new Set<number>()
    for (let tick = 0; tick < 1200; tick++) {
      const motion = clock.advance(100)
      expect({ ...motion, frame: 0 }).toEqual({ lean: 0, nod: 0, breath: 0, frame: 0 })
      expect(poseById.picked_up.files[motion.frame]).toBeTruthy()
      frames.add(motion.frame)
    }
    expect(frames.size).toBeGreaterThan(1)
  })
  it('varies blink spacing without frame jitter or a repeating eight-second loop', () => {
    let seed = 17
    const clock = new PoseClock('reading', () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 })
    const starts: number[] = []
    let previous = 0
    for (let i = 0; i < 2400; i++) {
      const { frame } = clock.advance(50)
      if (frame > 0 && previous === 0) starts.push(clock.seconds)
      previous = frame
    }
    const intervals = starts.slice(1).map((time, i) => time - starts[i])
    expect(intervals.length).toBeGreaterThan(10)
    expect(Math.max(...intervals) - Math.min(...intervals)).toBeGreaterThan(1)
    expect(intervals.every(gap => gap >= 3.1 && gap < 7.3)).toBe(true)
    expect(clock.seconds).toBeCloseTo(120)
  })
  it('cuts poses once, retains phase on rerenders, and caps background time', () => {
    const clock = new PoseClock()
    clock.advance(50)
    clock.select('standing')
    expect(clock.seconds).toBe(0.05)
    clock.select('reading')
    expect(clock.seconds).toBe(0)
    clock.advance(10000)
    expect(clock.seconds).toBe(0.1)
    clock.advance(100, false)
    expect(clock.seconds).toBe(0.1)
    expect(() => clock.advance(NaN)).toThrow()
  })
  it.each([0.7, 0.8, 1, 1.35])('keeps fixed head scale and floor across poses at native scale %s', (size) => {
    const width = 340 * size, height = 300 * size
    const transforms = ['standing', 'reading', 'dozing', 'picked_up'].map(id => posePlacement(poseById[id as 'standing'], width, height, 'right', 'side', null))
    expect(new Set(transforms.map(t => t.scaleY)).size).toBe(1)
    for (const t of transforms) expect(t.y + 560 * t.scaleY).toBeCloseTo(height * 0.94)
  })
  it.each(['left', 'right'] as const)('clips only at the actual %s viewport boundary', (side) => {
    const t = posePlacement(poseById.peeking, 272, 240, side === 'left' ? 'right' : 'left', 'side', side)
    expect(t.x + 220.44 * t.scaleX).toBeCloseTo(side === 'left' ? 0 : 272)
    const eyeX = t.x + 269.04 * t.scaleX
    expect(eyeX).toBeGreaterThan(0)
    expect(eyeX).toBeLessThan(272)
    expect(t.y + 71 * t.scaleY).toBeGreaterThan(0)
    expect(t.y + 405 * t.scaleY).toBeLessThan(240)
  })
  it('leaves space below an overhead bubble on narrow monitors', () => {
    const t = posePlacement(poseById.standing, 200, 240, 'left', 'top', null)
    expect(t.y + 71 * t.scaleY).toBeGreaterThan(100)
    expect(() => posePlacement(poseById.standing, 0, 240, 'left', 'side', null)).toThrow()
  })

  it.each([0.7, 0.8, 1, 1.35])('compacts unused space, not the character, at scale %s', (size) => {
    const width = 340 * size, height = 300 * size
    const layout = compactPeekLayout(width, height)
    expect(layout.width).toBeLessThan(width * 0.8)
    expect(layout.height).toBeLessThanOrEqual(height)
    expect(layout.width * layout.height).toBeLessThan(width * height * 0.8)
    expect(layout.bubbleInset + layout.bubbleWidth).toBeLessThan(layout.width)
    expect(layout.bubbleTop + PEEK_BUBBLE_MAX_HEIGHT).toBeLessThanOrEqual(layout.height)
    for (const side of ['left', 'right'] as const) {
      const full = posePlacement(poseById.peeking, width, height, 'right', 'side', side)
      const compact = posePlacement(poseById.peeking, layout.width, layout.height, 'right', 'side', side, layout)
      expect(compact.scaleY).toBe(full.scaleY)
      expect(compact.y).toBe(full.y)
      expect(compact.x + 220.44 * compact.scaleX).toBeCloseTo(side === 'left' ? 0 : layout.width)
      expect(compact.y + 409 * compact.scaleY).toBeLessThan(layout.height)
    }
  })
})
