import { useState, useRef, useEffect, useCallback } from 'react'

interface UsePanelResizeOptions {
  initialWidth: number
  minWidth?: number
  maxWidth?: number
}

export function usePanelResize({
  initialWidth,
  minWidth = 280,
  maxWidth = 700,
}: UsePanelResizeOptions) {
  const [width, setWidth] = useState(initialWidth)
  const [dragging, setDragging] = useState(false)
  const startXRef = useRef(0)
  const startWidthRef = useRef(initialWidth)

  useEffect(() => {
    setWidth(initialWidth)
    startWidthRef.current = initialWidth
  }, [initialWidth])

  useEffect(() => {
    if (!dragging) return

    const onMove = (e: MouseEvent) => {
      const delta = startXRef.current - e.clientX
      setWidth(Math.min(maxWidth, Math.max(minWidth, startWidthRef.current + delta)))
    }

    const onUp = () => setDragging(false)

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [dragging, minWidth, maxWidth])

  const onDragHandleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setDragging(true)
    startXRef.current = e.clientX
    startWidthRef.current = width
  }, [width])

  return { width, onDragHandleMouseDown, dragging }
}
