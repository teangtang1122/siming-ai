import { describe, expect, it } from 'vitest'
import sharedTimestamps from '../../../contracts/fixtures/api-timestamps-v1-interop.json'

import {
  apiDateTimeIso,
  formatApiDateTime,
  formatAssistantTimestamp,
  normalizeApiDateTime,
  parseApiDateTime,
} from '../utils/dateTime'

describe('API datetime handling', () => {
  it.each(sharedTimestamps.cases)('matches the PC/mobile timestamp contract: $id', (testCase) => {
    expect(parseApiDateTime(testCase.input)?.getTime() ?? null).toBe(testCase.epoch_ms)
    expect(formatApiDateTime(testCase.input, testCase.zone)).toBe(testCase.display)
  })

  it('treats legacy timezone-less database values as UTC', () => {
    expect(normalizeApiDateTime('2026-08-14T15:30:00')).toBe('2026-08-14T15:30:00Z')
    expect(apiDateTimeIso('2026-08-14 15:30:00.123456')).toBe('2026-08-14T15:30:00.123Z')
  })

  it('preserves timestamps that already contain an explicit offset', () => {
    const utc = parseApiDateTime('2026-08-14T15:30:00+00:00')
    const china = parseApiDateTime('2026-08-14T23:30:00+08:00')
    expect(utc?.getTime()).toBe(china?.getTime())
  })

  it('formats old and new records identically in the reader timezone', () => {
    const options = {
      now: new Date('2026-08-14T15:45:00Z'),
      timeZone: 'Asia/Shanghai',
    }
    expect(formatAssistantTimestamp('2026-08-14T15:30:00', options)).toBe('23:30')
    expect(formatAssistantTimestamp('2026-08-14T15:30:00+00:00', options)).toBe('23:30')
    expect(formatAssistantTimestamp('2026-08-14T23:30:00+08:00', options)).toBe('23:30')
  })

  it('adds day context to history without cluttering messages from today', () => {
    expect(formatAssistantTimestamp('2026-08-14T15:30:00Z', {
      now: new Date('2026-08-15T04:00:00Z'),
      timeZone: 'Asia/Shanghai',
    })).toBe('昨天 23:30')
    expect(formatAssistantTimestamp('2025-08-14T15:30:00Z', {
      now: new Date('2026-08-15T04:00:00Z'),
      timeZone: 'Asia/Shanghai',
    })).toBe('2025年8月14日 23:30')
  })

  it('rejects empty and invalid timestamps', () => {
    expect(parseApiDateTime('')).toBeNull()
    expect(parseApiDateTime('not-a-date')).toBeNull()
  })
})
