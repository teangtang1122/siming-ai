import { useCallback, useEffect, useRef, useState } from 'react'

export const PET_SPEECH_VISIBLE_MS = 5_000
export const PET_SPEECH_FADE_MS = 180

/** Only an explicit tap reveals speech; task/pose/hover changes never restart it. */
export function useDesktopPetSpeech() {
  const [phase, setPhase] = useState<'hidden' | 'visible' | 'fading'>('hidden')
  const timers = useRef<number[]>([])
  const clearTimers = useCallback(() => {
    timers.current.forEach(timer => window.clearTimeout(timer))
    timers.current = []
  }, [])

  useEffect(() => clearTimers, [clearTimers])

  const reveal = useCallback(() => {
    clearTimers()
    setPhase('visible')
    timers.current = [
      window.setTimeout(() => setPhase('fading'), PET_SPEECH_VISIBLE_MS),
      window.setTimeout(() => {
        setPhase('hidden')
        timers.current = []
      }, PET_SPEECH_VISIBLE_MS + PET_SPEECH_FADE_MS),
    ]
  }, [clearTimers])

  return { phase, reveal }
}
