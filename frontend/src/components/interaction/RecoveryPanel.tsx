import { Alert, Button, Space } from 'antd'

export function RecoveryPanel({
  title,
  description,
  retryLabel = '重试',
  onRetry,
  secondaryLabel,
  onSecondary,
}: {
  title: string
  description?: string
  retryLabel?: string
  onRetry?: () => void
  secondaryLabel?: string
  onSecondary?: () => void
}) {
  return (
    <Alert
      type="error"
      showIcon
      role="alert"
      message={title}
      description={description}
      action={(
        <Space wrap>
          {onRetry && <Button type="primary" onClick={onRetry}>{retryLabel}</Button>}
          {onSecondary && secondaryLabel && <Button onClick={onSecondary}>{secondaryLabel}</Button>}
        </Space>
      )}
    />
  )
}
