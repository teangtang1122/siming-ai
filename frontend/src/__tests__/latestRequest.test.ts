import { describe, expect, it } from 'vitest'

import { createLatestRequestGate } from '../shared/latestRequest'

describe('createLatestRequestGate', () => {
  it('only lets the latest request write back', () => {
    const gate = createLatestRequestGate<string>()
    const first = gate.begin('first')
    const second = gate.begin('second')

    expect(gate.isCurrent(first)).toBe(false)
    expect(gate.isCurrent(second)).toBe(true)
  })

  it('invalidates an in-flight request when the UI enters a local-only state', () => {
    const gate = createLatestRequestGate<string>()
    const request = gate.begin('saved-record')

    gate.invalidate()

    expect(gate.isCurrent(request)).toBe(false)
  })
})
