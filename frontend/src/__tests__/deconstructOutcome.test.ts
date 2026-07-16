import { describe, expect, it } from 'vitest'

import { deconstructReportOutcome } from '../pages/deconstruct'
import type { DeconstructReport } from '../pages/deconstruct'

function report(overrides: Partial<DeconstructReport>): DeconstructReport {
  return {
    id: 'report-1',
    title: '测试拆书',
    status: 'completed',
    phase: 'completed',
    total_chunks: 10,
    completed_chunks: 10,
    failed_chunks: 0,
    total_words: 24000,
    created_at: new Date().toISOString(),
    ...overrides,
  }
}

describe('deconstructReportOutcome', () => {
  it('exposes failed chunks as a partial result', () => {
    const outcome = deconstructReportOutcome(report({ completed_chunks: 8, failed_chunks: 2 }))

    expect(outcome?.outcome).toBe('partial_success')
    expect(outcome?.result.completed).toContain('8 个分块已完成')
    expect(outcome?.result.incomplete).toContain('2 个分块未成功解析')
  })

  it('keeps completed chunks when the final merge fails', () => {
    const outcome = deconstructReportOutcome(report({
      status: 'failed',
      phase: 'failed',
      completed_chunks: 7,
      failed_chunks: 1,
      reduce_error: '合并结果格式不完整',
    }))

    expect(outcome?.outcome).toBe('partial_success')
    expect(outcome?.result.summary).toBe('合并结果格式不完整')
    expect(outcome?.result.incomplete).toContain('完整报告尚未生成')
  })
})
