export function isNovelInterviewRetryIntent(value: string): boolean {
  return /^(\u7ee7\u7eed|\u91cd\u8bd5|\u518d\u8bd5\u4e00\u6b21|\u91cd\u65b0\u8bd5\u8bd5|[?\uff1f\u55ef])$/i.test(value.trim())
}

interface InterviewErrorDetail {
  message?: string
  failure_class?: string
  next_action?: string
  runtime?: Record<string, unknown>
}

export function extractNovelInterviewErrorDetail(error: unknown): InterviewErrorDetail {
  if (!error || typeof error !== 'object') {
    return { message: error instanceof Error ? error.message : '' }
  }
  const response = (error as { response?: { data?: { detail?: unknown } } }).response
  const detail = response?.data?.detail
  if (detail && typeof detail === 'object') return detail as InterviewErrorDetail
  if (typeof detail === 'string') return { message: detail }
  return { message: error instanceof Error ? error.message : '' }
}

export function formatNovelInterviewError(error: unknown): string {
  const payload = extractNovelInterviewErrorDetail(error)
  const detail = payload.message || (error instanceof Error ? error.message : '') || '\u5f53\u524d\u6a21\u578b\u6ca1\u6709\u5b8c\u6210\u52a8\u6001\u91c7\u8bbf\u3002'
  const nextAction = payload.next_action || '\u672c\u8f6e\u56de\u7b54\u5df2\u4fdd\u7559\u3002\u53ef\u4ee5\u53d1\u9001\u201c\u7ee7\u7eed\u201d\u91cd\u8bd5\uff0c\u6216\u53d1\u9001\u201c\u76f4\u63a5\u751f\u6210\u201d\u8df3\u8fc7\u91c7\u8bbf\u3002'
  return `${detail}\n\n${nextAction}`
}

export function formatSystemAssistantError(error: unknown): string {
  const detail = error instanceof Error && error.message
    ? error.message
    : '\u5f53\u524d\u6a21\u578b\u6ca1\u6709\u5b8c\u6210\u56de\u590d\u3002'
  return `${detail}\n\n\u672c\u8f6e\u6ca1\u6709\u5b8c\u6210\u3002\u8bf7\u91cd\u8bd5\uff0c\u6216\u5728\u7cfb\u7edf\u8bbe\u7f6e\u4e2d\u6d4b\u8bd5\u5f53\u524d\u6a21\u578b/CLI\u3002`
}

export const NOVEL_INTERVIEW_THINKING = '\u6b63\u5728\u7ed3\u5408\u4f60\u7684\u56de\u7b54\u51b3\u5b9a\u4e0b\u4e00\u95ee\uff0c\u6216\u76f4\u63a5\u8fdb\u5165\u65b9\u6848\u751f\u6210...'
