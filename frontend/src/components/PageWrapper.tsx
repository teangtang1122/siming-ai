import type { CSSProperties, ReactNode } from 'react'

interface PageWrapperProps {
  children: ReactNode
  /** Max width of the content area. Defaults to 1200. */
  maxWidth?: number
  /** Padding around the content. Defaults to 24. */
  padding?: number
  /** Additional class name */
  className?: string
  /** Additional inline styles */
  style?: CSSProperties
}

/**
 * Shared page layout wrapper providing consistent padding and max-width
 * across all pages. Centers content horizontally.
 */
function PageWrapper({ children, maxWidth = 1200, padding = 24, className, style }: PageWrapperProps) {
  return (
    <div
      className={className}
      style={{
        padding,
        maxWidth,
        margin: '0 auto',
        width: '100%',
        ...style,
      }}
    >
      {children}
    </div>
  )
}

export default PageWrapper
