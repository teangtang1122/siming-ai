export function isNovelInterviewRetryIntent(value: string): boolean {
  return /^(继续|重试|再试一次|重新试试|[?？])$/i.test(value.trim())
}

export function formatNovelInterviewError(error: unknown): string {
  const detail = error instanceof Error && error.message
    ? error.message
    : '当前模型没有完成动态采访。'
  return `${detail}\n\n本轮回答已保留。可以发送“继续”重试，或发送“直接生成”跳过采访。`
}

export function formatSystemAssistantError(error: unknown): string {
  const detail = error instanceof Error && error.message
    ? error.message
    : '当前模型没有完成回复。'
  return `${detail}\n\n本轮没有完成。请重试，或在系统设置中测试当前模型/CLI。`
}

export const NOVEL_INTERVIEW_THINKING = '正在结合你的回答决定下一问，或直接进入方案生成...'
