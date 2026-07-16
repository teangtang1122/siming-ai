import { Alert, Button, Space, Typography } from 'antd'
import type { OperationAttention, OperationOutcome, OperationResult } from './types'
import { outcomeMeta } from './status'

const { Text } = Typography

interface PersistentOutcomeProps {
  outcome: OperationOutcome
  result?: OperationResult | null
  attention?: OperationAttention | null
  title?: string
  description?: string
  actionLabel?: string
  onAction?: () => void
  className?: string
}
export function PersistentOutcome({
  outcome,
  result,
  attention,
  title,
  description,
  actionLabel,
  onAction,
  className,
}: PersistentOutcomeProps) {
  const meta = outcomeMeta[outcome]
  const completed = result?.completed || []
  const incomplete = result?.incomplete || []
  const warnings = result?.warnings || []
  const details = description || attention?.message || result?.summary
  return (
    <Alert
      className={className}
      type={meta.alertType}
      showIcon
      role={outcome === 'failed' ? 'alert' : 'status'}
      message={title || attention?.title || meta.label}
      description={(
        <Space direction="vertical" size={5}>
          {details && <Text>{details}</Text>}
          {completed.length > 0 && <Text type="secondary">已完成：{completed.join('；')}</Text>}
          {incomplete.length > 0 && <Text type="secondary">未完成：{incomplete.join('；')}</Text>}
          {warnings.length > 0 && <Text type="warning">注意：{warnings.join('；')}</Text>}
        </Space>
      )}
      action={onAction ? <Button onClick={onAction}>{actionLabel || attention?.action_label || '继续处理'}</Button> : undefined}
    />
  )
}
