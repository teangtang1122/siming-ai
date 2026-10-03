import type { Pose, PoseId } from './contract'

export interface PoseMotion { lean: number; nod: number; breath: number; frame: number }
export const smooth = (value: number) => {
  const t = Math.max(0, Math.min(1, value))
  return t * t * (3 - 2 * t)
}
const still = (): PoseMotion => ({ lean: 0, nod: 0, breath: 0, frame: 0 })

export function blinkFrame(id: PoseId, elapsed: number) {
  if (id === 'dozing' || elapsed < 0 || elapsed >= 0.25) return 0
  return id === 'standing' ? Math.min(4, 1 + Math.floor(elapsed / 0.0625))
    : elapsed < 0.06 ? 1 : elapsed < 0.15 ? 2 : elapsed < 0.24 ? 1 : 0
}

export function deformPoint([x, y]: [number, number], pose: Pose, motion: PoseMotion) {
  const upper = 1 - smooth((y - Math.min(pose.hipY - 20, 480)) / (540 - Math.min(pose.hipY - 20, 480)))
  const head = 1 - smooth((y - pose.neckY + 8) / 93)
  return [x + motion.lean * upper, y + motion.nod * head + motion.breath * upper]
}

// Randomness schedules occasional gestures, never per-frame jitter. Most of
// the time there is only subtle breathing on the same connected mesh.
export class PoseClock {
  current: PoseId
  seconds = 0
  private nextBlink = 0
  private blinkAt = -Infinity
  private nextGesture = 0
  private gestureAt = -Infinity
  private gestureDuration = 3.8
  private direction = 1
  private breathPeriod = 5.6
  constructor(initial: PoseId = 'standing', private readonly random: () => number = Math.random) {
    this.current = initial
    this.schedule()
  }
  private between(low: number, high: number) { return low + (high - low) * this.random() }
  private schedule() {
    this.nextBlink = this.between(2.5, 6.5)
    this.nextGesture = this.between(7, 15)
    this.blinkAt = this.gestureAt = -Infinity
    this.breathPeriod = this.between(4.8, 6.5)
  }
  select(id: PoseId) {
    if (this.current !== id) { this.current = id; this.seconds = 0; this.schedule() }
  }
  advance(milliseconds: number, enabled = true): PoseMotion {
    if (!Number.isFinite(milliseconds) || milliseconds < 0) throw new RangeError('Invalid clock delta')
    if (!enabled) return still()
    this.seconds += Math.min(milliseconds, 100) / 1000
    const t = this.seconds, id = this.current
    if (t >= this.nextBlink) {
      this.blinkAt = t
      this.nextBlink = t + this.between(3.1, 7.2)
    }
    if (t >= this.nextGesture) {
      this.gestureAt = t
      this.gestureDuration = id === 'peeking' ? this.between(3.4, 4.8) : 2.3
      this.direction = this.random() < 0.5 ? -1 : 1
      this.nextGesture = t + this.gestureDuration + this.between(8, 16)
    }
    const motion = still()
    motion.frame = blinkFrame(id, t - this.blinkAt)
    // Suspension is drawn into the new complete pose. Do not reuse grounded
    // breathing/nodding deformations that make its sleeves or torso swim.
    if (id === 'picked_up') return motion
    motion.breath = (1 - Math.cos(t * Math.PI * 2 / this.breathPeriod)) / 2 * (id === 'dozing' ? 1.2 : 0.65)
    const gesture = (t - this.gestureAt) / this.gestureDuration
    if (gesture >= 0 && gesture < 1) {
      if (id === 'peeking') {
        const lean = smooth(gesture / 0.28) * (1 - smooth((gesture - 0.65) / 0.35))
        motion.lean = 3 * lean
        motion.nod = -0.6 * lean
      } else {
        const nod = Math.sin(gesture * Math.PI) ** 2
        motion.nod = nod * (id === 'reading' ? 4.6 : id === 'dozing' ? 3.2 : 1.4)
        if (id === 'standing') motion.lean = this.direction * nod * 1.5
      }
    }
    return motion
  }
}
