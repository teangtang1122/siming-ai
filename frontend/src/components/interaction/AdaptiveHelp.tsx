import { useState } from 'react'
import { Alert } from 'antd'
import { CloseOutlined } from '@ant-design/icons'

export function AdaptiveHelp({
  preferenceKey,
  title,
  description,
}: {
  preferenceKey: string
  title: string
  description: string
}) {
  const storageKey = `siming_help_dismissed_${preferenceKey}`
  const [visible, setVisible] = useState(() => localStorage.getItem(storageKey) !== 'true')
  if (!visible) return null
  return (
    <Alert
      className="siming-adaptive-help"
      type="info"
      showIcon
      closable
      closeIcon={<span aria-label="关闭提示"><CloseOutlined /></span>}
      message={title}
      description={description}
      onClose={() => {
        localStorage.setItem(storageKey, 'true')
        setVisible(false)
      }}
    />
  )
}
