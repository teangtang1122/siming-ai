import type { ReactNode } from 'react'
import { Button, Empty } from 'antd'

export function AuthorEmptyState({
  description,
  actionLabel,
  onAction,
  image,
}: {
  description: ReactNode
  actionLabel?: string
  onAction?: () => void
  image?: ReactNode
}) {
  return (
    <Empty
      image={image || Empty.PRESENTED_IMAGE_SIMPLE}
      description={description}
    >
      {onAction && actionLabel && <Button type="primary" onClick={onAction}>{actionLabel}</Button>}
    </Empty>
  )
}
