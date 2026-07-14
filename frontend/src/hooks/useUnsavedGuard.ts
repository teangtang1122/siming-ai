import { useEffect, useRef, useCallback, useState } from 'react'
import { Modal } from 'antd'

/**
 * 拦截浏览器关闭/刷新，防止未保存数据丢失。
 * 返回 markSaved 需要在保存成功后调用以清除脏状态。
 *
 * 用法：
 *   const { markDirty, markSaved } = useUnsavedGuard()
 *   // 表单 onChange 时调 markDirty()
 *   // 保存成功后调 markSaved()
 */
export function useUnsavedGuard() {
  const dirtyRef = useRef(false)
  const [isDirty, setIsDirty] = useState(false)

  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (dirtyRef.current) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  const markDirty = useCallback(() => {
    dirtyRef.current = true
    setIsDirty(true)
  }, [])

  const markSaved = useCallback(() => {
    dirtyRef.current = false
    setIsDirty(false)
  }, [])

  /** 如果有未保存更改，弹窗确认；确认后执行 callback */
  const confirmLeave = useCallback((callback: () => void) => {
    if (!dirtyRef.current) {
      callback()
      return
    }
    Modal.confirm({
      title: '未保存的更改',
      content: '当前页面有未保存的修改，确定要离开吗？',
      okText: '离开',
      cancelText: '留下',
      okButtonProps: { danger: true },
      onOk: () => {
        dirtyRef.current = false
        setIsDirty(false)
        callback()
      },
    })
  }, [])

  return { isDirty, markDirty, markSaved, confirmLeave }
}
