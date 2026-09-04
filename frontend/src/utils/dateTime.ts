const EXPLICIT_TIME_ZONE = /(?:z|[+-]\d{2}:?\d{2})$/i
const NAIVE_ISO_DATE_TIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/

export interface AssistantTimestampOptions {
  now?: Date
  timeZone?: string
}

interface DateTimeParts {
  year: number
  month: number
  day: number
  hour: string
  minute: string
}

/**
 * Normalize API timestamps for browsers.
 *
 * Siming stores database datetimes as UTC. Older releases serialized those
 * values without an offset, which browsers interpret as local wall-clock
 * time. Keep explicit offsets untouched and treat legacy naive ISO values as
 * UTC so all saved records represent the same instant on every client.
 */
export function normalizeApiDateTime(value?: string | null): string | null {
  const normalized = String(value || '').trim()
  if (!normalized) return null
  if (NAIVE_ISO_DATE_TIME.test(normalized) && !EXPLICIT_TIME_ZONE.test(normalized)) {
    return `${normalized.replace(' ', 'T')}Z`
  }
  return normalized
}

export function parseApiDateTime(value?: string | null): Date | null {
  const normalized = normalizeApiDateTime(value)
  if (!normalized) return null
  const parsed = new Date(normalized)
  return Number.isFinite(parsed.getTime()) ? parsed : null
}

export function apiDateTimeMs(value?: string | null): number {
  return parseApiDateTime(value)?.getTime() ?? Number.NaN
}

export function apiDateTimeIso(value?: string | null): string | null {
  return parseApiDateTime(value)?.toISOString() ?? null
}

function dateTimeParts(date: Date, timeZone?: string): DateTimeParts {
  const formatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone,
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  })
  const parts = Object.fromEntries(
    formatter.formatToParts(date).map((part) => [part.type, part.value]),
  )
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    day: Number(parts.day),
    hour: parts.hour,
    minute: parts.minute,
  }
}

function calendarDay(parts: DateTimeParts): number {
  return Date.UTC(parts.year, parts.month - 1, parts.day)
}

/** Compact, local and history-aware timestamp shared by both AI assistants. */
export function formatAssistantTimestamp(
  value?: string | null,
  options: AssistantTimestampOptions = {},
): string | null {
  const date = parseApiDateTime(value)
  if (!date) return null
  const current = options.now || new Date()
  const parts = dateTimeParts(date, options.timeZone)
  const currentParts = dateTimeParts(current, options.timeZone)
  const time = `${parts.hour}:${parts.minute}`
  const dayDifference = Math.round(
    (calendarDay(currentParts) - calendarDay(parts)) / 86_400_000,
  )
  if (dayDifference === 0) return time
  if (dayDifference === 1) return `昨天 ${time}`
  if (parts.year === currentParts.year) return `${parts.month}月${parts.day}日 ${time}`
  return `${parts.year}年${parts.month}月${parts.day}日 ${time}`
}

/** Full local timestamp for saved records, versions and message tooltips. */
export function formatApiDateTime(
  value?: string | null,
  timeZone?: string,
): string | null {
  const date = parseApiDateTime(value)
  if (!date) return null
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).format(date)
}
