import { CheckCircleOutlined, CloseCircleOutlined, CloudSyncOutlined, EditOutlined } from '@ant-design/icons'
import { Space, Tag, Typography } from 'antd'
import type { ReactNode } from 'react'
import type { SaveStatus } from './types'
import { saveStatusMeta } from './status'

const icons: Record<SaveStatus, ReactNode> = {
  saved: <CheckCircleOutlined />,
  dirty: <EditOutlined />,
  saving: <CloudSyncOutlined spin />,
  error: <CloseCircleOutlined />,
}

export function SaveStatusIndicator({ status, error }: { status: SaveStatus; error?: string | null }) {
  const meta = saveStatusMeta[status]
  return (
    <Space size={7} wrap role="status" aria-live="polite">
      <Tag color={meta.color}>
        <Space size={5}>{icons[status]}<span>{meta.label}</span></Space>
      </Tag>
      {status === 'error' && error && <Typography.Text type="danger">{error}</Typography.Text>}
    </Space>
  )
}
