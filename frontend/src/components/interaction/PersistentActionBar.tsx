import type { ReactNode } from 'react'

export function PersistentActionBar({
  children,
  label = '当前步骤操作',
  className = '',
}: {
  children: ReactNode
  label?: string
  className?: string
}) {
  return (
    <div className={`siming-action-bar ${className}`.trim()} role="region" aria-label={label}>
      {children}
    </div>
  )
}
