import { describe, expect, it } from 'vitest'
import { parseModelRequestParameters } from '../features/settings/modelRequestParameters'

describe('model-specific request parameters', () => {
  it('keeps models and explicit omission separate', () => {
    expect(parseModelRequestParameters({ 'gpt-6-luna': '{"temperature":null}', other: '{"temperature":0.8}' }))
      .toEqual({ 'gpt-6-luna': { temperature: null }, other: { temperature: 0.8 } })
  })
  it('supports automatic settings and rejects malformed or non-object data', () => {
    expect(parseModelRequestParameters({ m: '{}' })).toEqual({ m: {} })
    for (const raw of ['[]', 'null', '1', '{bad']) expect(() => parseModelRequestParameters({ m: raw })).toThrow()
  })
})
