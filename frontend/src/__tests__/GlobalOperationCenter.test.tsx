import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('../api/client', () => ({ apiClient: api }))

import GlobalOperationCenter from '../components/GlobalOperationCenter'

class FakeEventSource {
  static last: FakeEventSource | undefined
  readonly url: string
  onopen: ((event: Event) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  listeners = new Map<string, (event: Event) => void>()

  constructor(url: string | URL) {
    this.url = String(url)
    FakeEventSource.last = this
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    const callback = typeof listener === 'function' ? listener : listener.handleEvent.bind(listener)
    this.listeners.set(type, callback)
  }

  close() {}
}

const operation = {
  id: 'operation-1',
  source_kind: 'cataloging',
  title: '作品建档 · 第151章',
  status: 'running',
  health_status: 'suspected_stall',
  phase: 'chapter_archive',
  current_message: '正在检查第151章的角色状态',
  progress: { mode: 'indeterminate', current: null, total: null, percent: null },
  model_source: 'opencode_cli:opencode/deepseek-v4-flash-free',
  next_action: '可以继续等待，或只重试当前章节',
  resume_url: '/project/project-1?view=cataloging',
  can_pause: true,
  can_cancel: true,
  can_retry: true,
  elapsed_seconds: 2234,
  last_activity_at: new Date().toISOString(),
}

describe('GlobalOperationCenter', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    FakeEventSource.last = undefined
    vi.stubGlobal('EventSource', FakeEventSource)
    api.get.mockResolvedValue({ data: { data: { items: [operation] } } })
    api.post.mockResolvedValue({ data: { data: operation } })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('shows real health and avoids a fabricated percentage for indeterminate work', async () => {
    render(<MemoryRouter><GlobalOperationCenter /></MemoryRouter>)

    const trigger = await screen.findByRole('button', { name: '全局任务中心，1 个任务正在运行' })
    fireEvent.click(trigger)

    expect(await screen.findByText('疑似停滞')).toBeInTheDocument()
    expect(screen.getByText('正在等待下一条真实活动，不估算完成百分比')).toBeInTheDocument()
    expect(screen.getByText(/opencode_cli:opencode\/deepseek-v4-flash-free/)).toBeInTheDocument()
    expect(screen.queryByText(/\d+%/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /重试当前单元/ }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/operations/operation-1/retry-current-unit'))
  })

  it('treats an SSE break as reconnection while polling remains active', async () => {
    render(<MemoryRouter><GlobalOperationCenter /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: '全局任务中心，1 个任务正在运行' }))
    await waitFor(() => expect(FakeEventSource.last?.url).toBe('/api/v1/operations/operation-1/stream'))

    act(() => FakeEventSource.last?.onerror?.(new Event('error')))

    expect(await screen.findByText('进度流正在重新连接，已改用状态轮询')).toBeInTheDocument()
    expect(screen.getByText('作品建档 · 第151章')).toBeInTheDocument()
  })
})
