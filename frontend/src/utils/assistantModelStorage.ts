const LEGACY_SHARED_MODEL_KEYS = ['siming.assistant.model', 'moshu.assistant.model']

export const GLOBAL_MODEL_SELECT_VALUE = '__global_default__'

export function readModelOverride(storageKey: string): string | undefined {
  try {
    const value = localStorage.getItem(storageKey) || undefined
    LEGACY_SHARED_MODEL_KEYS.forEach((key) => localStorage.removeItem(key))
    return value
  } catch {
    return undefined
  }
}

export function writeModelOverride(storageKey: string, value?: string) {
  try {
    if (value) {
      localStorage.setItem(storageKey, value)
    } else {
      localStorage.removeItem(storageKey)
    }
    LEGACY_SHARED_MODEL_KEYS.forEach((key) => localStorage.removeItem(key))
  } catch {
    // Ignore storage errors from private browsing or locked-down environments.
  }
}

export function globalModelOptionLabel(defaultModel?: string) {
  return defaultModel ? `跟随全局默认 · ${defaultModel}` : '跟随全局默认'
}
