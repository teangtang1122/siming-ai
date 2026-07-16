import { useEffect, useRef, useCallback, useState } from 'react'
import { Modal } from 'antd'
import type { SaveStatus } from '../components/interaction'

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
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('saved')
  const [saveError, setSaveError] = useState<string | null>(null)

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
    setSaveStatus('dirty')
    setSaveError(null)
  }, [])

  const markSaved = useCallback(() => {
    dirtyRef.current = false
    setIsDirty(false)
    setSaveStatus('saved')
    setSaveError(null)
  }, [])

  const markSaving = useCallback(() => {
    setSaveStatus('saving')
    setSaveError(null)
  }, [])

  const markSaveFailed = useCallback((error?: string) => {
    dirtyRef.current = true
    setIsDirty(true)
    setSaveStatus('error')
    setSaveError(error || '保存失败，请重试。')
  }, [])

  const confirmLeave = useCallback((callback: () => void) => {
    if (!dirtyRef.current) {
      callback()
      return
    }
    Modal.confirm({
      title: '还有未保存的修改',
      content: '离开后，这些修改不会保留。你可以先留下并保存，或确认放弃修改。',
      okText: '放弃并离开',
      cancelText: '留下保存',
      okButtonProps: { danger: true },
      onOk: () => {
        dirtyRef.current = false
        setIsDirty(false)
        setSaveStatus('saved')
        setSaveError(null)
        callback()
      },
    })
  }, [])

  return {
    isDirty,
    saveStatus,
    saveError,
    markDirty,
    markSaved,
    markSaving,
    markSaveFailed,
    confirmLeave,
  }
}
