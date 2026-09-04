export interface ModelContextCapacityIssue {
  code: 'conversation_capacity_unknown'
  message: string
  remediation: 'configure_model_context_profile'
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

/**
 * Read only the stable context-error envelope. Do not infer a model window
 * from provider/model names or from an arbitrary error string.
 */
export function modelContextCapacityIssueFrom(value: unknown): ModelContextCapacityIssue | null {
  const root = record(value)
  if (!root) return null
  const response = record(root.response)
  const responseData = record(response?.data)
  const candidates = [
    root,
    record(root.data),
    record(root.detail),
    responseData,
    record(responseData?.detail),
  ]
  for (const candidate of candidates) {
    if (!candidate || candidate.code !== 'conversation_capacity_unknown') continue
    const details = record(candidate.details)
    if (details?.remediation !== 'configure_model_context_profile') continue
    return {
      code: 'conversation_capacity_unknown',
      message: String(candidate.message || '当前模型没有经过验证的上下文容量档案。'),
      remediation: 'configure_model_context_profile',
    }
  }
  return null
}
