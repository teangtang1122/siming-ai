import type { OperationOutcome, SaveStatus } from './types'

export const outcomeMeta: Record<OperationOutcome, {
  label: string
  alertType: 'success' | 'info' | 'warning' | 'error'
}> = {
  completed_with_reply: { label: '已收到回复', alertType: 'success' },
  completed_with_tools: { label: '数据已更新', alertType: 'success' },
  partial_success: { label: '部分完成', alertType: 'warning' },
  empty_response: { label: '没有收到结果', alertType: 'warning' },
  skipped_preflight: { label: '尚未执行', alertType: 'info' },
  waiting_user: { label: '等待你的处理', alertType: 'info' },
  blocked: { label: '当前被阻塞', alertType: 'warning' },
  failed: { label: '执行失败', alertType: 'error' },
  cancelled: { label: '已取消', alertType: 'info' },
  interrupted: { label: '运行已中断', alertType: 'warning' },
}
export const saveStatusMeta: Record<SaveStatus, {
  label: string
  color: 'success' | 'warning' | 'processing' | 'error'
}> = {
  saved: { label: '已保存', color: 'success' },
  dirty: { label: '有未保存修改', color: 'warning' },
  saving: { label: '保存中', color: 'processing' },
  error: { label: '保存失败', color: 'error' },
}
