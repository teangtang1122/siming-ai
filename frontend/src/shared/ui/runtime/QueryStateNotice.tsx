import { Alert, Button } from 'antd'

interface QueryStateNoticeProps {
  error: unknown
  title?: string
  onRetry?: () => void
}

export function QueryStateNotice({ error, title = '暂时无法读取数据', onRetry }: QueryStateNoticeProps) {
  const description = error instanceof Error ? error.message : '请检查连接后重试。'
  return (
    <Alert
      type="error"
      showIcon
      role="alert"
      message={title}
      description={description}
      action={onRetry ? <Button onClick={onRetry}>重新加载</Button> : undefined}
    />
  )
}
