import { Alert, Button } from 'antd'
import { SettingOutlined } from '@ant-design/icons'
import type { ModelContextCapacityIssue } from '../../services/conversationContextErrors'

interface ModelContextCapacityAlertProps {
  issue: ModelContextCapacityIssue | null
  onConfigure: () => void
}

export function ModelContextCapacityAlert({ issue, onConfigure }: ModelContextCapacityAlertProps) {
  if (!issue) return null
  return (
    <Alert
      className="model-context-capacity-alert"
      data-testid="model-context-capacity-alert"
      type="warning"
      showIcon
      message="需要配置当前模型的上下文容量"
      description={`${issue.message} 为避免用猜测的容量继续执行，本轮业务工具尚未运行。`}
      action={(
        <Button
          aria-label="配置上下文容量"
          size="small"
          type="primary"
          icon={<SettingOutlined />}
          onClick={onConfigure}
        >
          配置上下文容量
        </Button>
      )}
    />
  )
}
