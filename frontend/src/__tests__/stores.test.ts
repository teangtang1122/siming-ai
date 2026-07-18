import { beforeEach, describe, expect, it } from 'vitest'
import { useAppStore } from '../stores'

describe('useAppStore', () => {
  beforeEach(() => {
    useAppStore.setState({ error: null })
  })

  it('stores only cross-page UI errors', () => {
    useAppStore.getState().setError('网络连接中断')
    expect(useAppStore.getState().error).toBe('网络连接中断')
  })

  it('clears the global error banner state', () => {
    useAppStore.setState({ error: '保存失败' })
    useAppStore.getState().setError(null)
    expect(useAppStore.getState().error).toBeNull()
  })
})
