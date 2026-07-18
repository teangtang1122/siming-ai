import { Space, Tag } from 'antd'
import type { OperationRun } from '../../api/contracts'

const lifecycleLabels: Record<OperationRun['status'], string> = {
  draft: '草稿',
  queued: '等待开始',
  running: '运行中',
  waiting_user: '等待确认',
  paused: '已暂停',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
}

const healthMeta: Record<OperationRun['health_status'], { label: string; color: string }> = {
  active: { label: '正在推进', color: 'success' },
  quiet: { label: '模型仍在计算', color: 'processing' },
  suspected_stall: { label: '疑似停滞', color: 'warning' },
  stalled: { label: '已卡住', color: 'error' },
  disconnected: { label: '运行连接中断', color: 'error' },
}

const COMPUTING_STATUSES = new Set<OperationRun['status']>(['queued', 'running'])

export function RuntimeStatusTags({ operation }: { operation: OperationRun }) {
  const health = healthMeta[operation.health_status]
  return (
    <Space size={6} wrap aria-label="任务状态">
      <Tag>{lifecycleLabels[operation.status]}</Tag>
      {COMPUTING_STATUSES.has(operation.status) && (
        <Tag color={health.color}>{health.label}</Tag>
      )}
    </Space>
  )
}
