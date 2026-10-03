import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import fixture from '../../../contracts/fixtures/context-trace-v1-interop.json'
import { ContextInspectorHost } from '../features/contextInspector/ContextInspector'
import { buildSpans, formatTraceDuration } from '../features/contextInspector/types'
import type { TraceEvent } from '../features/contextInspector/types'
import { CONTEXT_INSPECTOR_OPEN } from '../shared/contextInspector'

const api = vi.hoisted(() => ({ listTraces: vi.fn(), traceHealth: vi.fn(), traceEvents: vi.fn(),
  traceDetail: vi.fn(), tracePayload: vi.fn(), setTraceMode: vi.fn(), clearTraces: vi.fn(), exportTrace: vi.fn() }))
vi.mock('../features/contextInspector/api', () => api)

const trace = (id: string) => ({ id, cursor: 1, scope_kind: 'creation_session', scope_id: id,
  started: 1, finished: 2, status: 'error', mode: 'summary', capture_status: 'recorded', dropped: 0, correlations: {} })
function open(id: string) {
  act(() => window.dispatchEvent(new CustomEvent(CONTEXT_INSPECTOR_OPEN, { detail: {
    scope: { kind: 'creation_session', id }, correlationId: id,
  } })))
}
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><ContextInspectorHost /></QueryClientProvider>)
}

beforeEach(() => {
  vi.resetAllMocks()
  api.traceHealth.mockResolvedValue({ policy: { mode: 'summary', full_until: null }, dropped_events: 0, write_errors: 0 })
  api.listTraces.mockImplementation(scope => Promise.resolve({ items: scope ? [trace(scope.id)] : [], next_cursor: null }))
  api.traceDetail.mockImplementation(id => Promise.resolve(trace(id)))
  api.traceEvents.mockResolvedValue(fixture.events)
})

describe('native context inspector', () => {
  it('does not leave interrupted or incompletely recorded steps running', () => {
    const events = fixture.events.filter(event => event.event_type === 'span_started') as TraceEvent[]
    expect(buildSpans(events, { finished: null, capture_status: 'recording' })[0].status).toBe('running')
    expect(buildSpans(events, { finished: 2, capture_status: 'interrupted' })[0].status).toBe('interrupted')
    expect(buildSpans(events, { finished: 2, capture_status: 'partial' })[0].status).toBe('incomplete')
    expect(buildSpans(events, { finished: 2, capture_status: 'recorded' }, false)[0].status).toBe('pending_record')
    expect(buildSpans(fixture.events as TraceEvent[], { finished: 2, capture_status: 'interrupted' })[0].status).toBe('error')
    expect(formatTraceDuration(0.2)).toBe('<1 ms')
    expect(formatTraceDuration(undefined)).toBe('耗时未知')
  })

  it('can enable continuous recording before any project or task exists', async () => {
    mount()
    act(() => window.dispatchEvent(new CustomEvent(CONTEXT_INSPECTOR_OPEN, { detail: {} })))
    expect(await screen.findByText('还没有调用记录。先开启完整记录，再去立项或与助手对话。')).toBeInTheDocument()
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '记录级别' }))
    fireEvent.click(screen.getByText('持续完整记录'))
    await waitFor(() => expect(api.setTraceMode).toHaveBeenCalledWith('full', false))
    expect(api.listTraces).toHaveBeenCalledWith(undefined, undefined, null, expect.any(AbortSignal))
  })

  it('can clear a message filter to view calls from creation and other tasks', async () => {
    mount(); open('creation-reply')
    expect(await screen.findByText('generate_creation_artifact')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '全部调用' }))
    expect(await screen.findByText('全部调用记录')).toBeInTheDocument()
    await waitFor(() => expect(api.listTraces).toHaveBeenCalledWith(undefined, undefined, null, expect.any(AbortSignal)))
    expect(screen.queryByText('generate_creation_artifact')).not.toBeInTheDocument()
  })

  it('uses source sequence even if timestamps run backwards; does not invent missing usage', () => {
    const events = fixture.events.map((event, index) => ({ ...event, timestamp: 100 - index })) as TraceEvent[]
    const spans = buildSpans(events)
    expect(spans).toHaveLength(1)
    expect(spans[0]).toMatchObject({ status: 'error', duration_ms: 10 })
    expect(spans[0].usage).toBeUndefined()
  })

  it('shows preflight failure without inventing a provider call or fetching unrecorded body', async () => {
    mount(); open('message-a')
    expect(await screen.findByText('generate_creation_artifact')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /送回模型的结果/ }))
    expect(await screen.findByText('当时未开启完整内容记录')).toBeInTheDocument()
    expect(api.tracePayload).not.toHaveBeenCalled()
    expect(screen.queryByText('API 请求')).not.toBeInTheDocument()
  })

  it('ignores a late previous-message response when the requested message changes', async () => {
    let resolveOld!: (value: unknown) => void
    api.traceEvents.mockImplementation(id => id === 'old' ? new Promise(resolve => { resolveOld = resolve }) : Promise.resolve([
      { ...fixture.events[0], event_id: 'new-event', data: { ...fixture.events[0].data, label: 'new-message-call' } },
    ]))
    mount(); open('old')
    await waitFor(() => expect(api.traceEvents).toHaveBeenCalledWith('old', 0, expect.any(AbortSignal)))
    open('new')
    expect(await screen.findByText('new-message-call')).toBeInTheDocument()
    await act(async () => resolveOld(fixture.events))
    expect(screen.queryByText('generate_creation_artifact')).not.toBeInTheDocument()
  })

  it('shows lookup failure instead of synthesizing an old request', async () => {
    api.listTraces.mockRejectedValue(new Error('记录已过期或不可访问'))
    mount(); open('missing')
    expect(await screen.findByText('记录已过期或不可访问')).toBeInTheDocument()
    expect(api.traceEvents).not.toHaveBeenCalled()
  })
})
