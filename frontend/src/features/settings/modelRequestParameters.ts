export const parseModelRequestParameters = (drafts?: Record<string, string>): Record<string, Record<string, unknown>> => {
  const result: Record<string, Record<string, unknown>> = Object.create(null)
  for (const [model, draft] of Object.entries(drafts || {})) {
    if (!model || model === '__new__') continue
    const parsed: unknown = JSON.parse(draft || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error(`${model} 的高级请求参数必须是 JSON 对象`)
    }
    result[model] = parsed as Record<string, unknown>
  }
  return result
}
