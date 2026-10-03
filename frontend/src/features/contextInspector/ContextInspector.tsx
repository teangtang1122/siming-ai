import { useEffect, useMemo, useState } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Drawer, Empty, Popconfirm, Select, Space, Spin, Tag, Typography } from 'antd'
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons'
import { CONTEXT_INSPECTOR_OPEN, openContextInspector } from '../../shared/contextInspector'
import type { ContextInspectorRequest } from '../../shared/contextInspector'
import { clearTraces, exportTrace, listTraces, setTraceMode, traceDetail, traceEvents, traceHealth, tracePayload } from './api'
import { buildSpans, formatTraceDuration, layerNames, missingNames, statusNames } from './types'
import type { ContextTrace, TraceEvent, TraceScope } from './types'
import './contextInspector.css'

const { Text } = Typography
const keyRoot = ['context-inspector'] as const
const scopeNames: Record<TraceScope['kind'], string> = {
  creation_session: '新书立项', project_conversation: '作品助手',
  system_conversation: '系统助手', operation: '后台任务',
}

function Failure({ error }: { error: unknown }) {
  return error ? <Alert type="error" showIcon message={error instanceof Error ? error.message : '调用记录读取失败'} /> : null
}

export function ContextInspectorHost() {
  const [request, setRequest] = useState<ContextInspectorRequest | null>(null)
  useEffect(() => {
    const open = (event: Event) => setRequest((event as CustomEvent<ContextInspectorRequest>).detail)
    window.addEventListener(CONTEXT_INSPECTOR_OPEN, open)
    return () => window.removeEventListener(CONTEXT_INSPECTOR_OPEN, open)
  }, [])
  return <Drawer title="调用与上下文" width="min(1180px, 96vw)" open={request !== null} onClose={() => setRequest(null)} destroyOnClose>
    {request && <ContextInspector key={JSON.stringify(request)} {...request} />}
  </Drawer>
}

export function ContextInspector({ scope, correlationId }: { scope?: TraceScope; correlationId?: string }) {
  const cache = useQueryClient()
  const [selected, setSelected] = useState<ContextTrace | null>(null)
  const settings = useQuery({ queryKey: [...keyRoot, 'settings'], queryFn: ({ signal }) => traceHealth(signal) })
  const traces = useInfiniteQuery({
    queryKey: [...keyRoot, 'list', scope, correlationId], initialPageParam: null as number | null,
    queryFn: ({ pageParam, signal }) => listTraces(scope, correlationId, pageParam, signal),
    getNextPageParam: last => last.next_cursor ?? undefined,
  })
  const changeMode = useMutation({
    mutationFn: (value: string) => setTraceMode(value === 'timed' ? 'full' : value as 'off' | 'summary' | 'full', value === 'timed'),
    onSuccess: () => cache.invalidateQueries({ queryKey: [...keyRoot, 'settings'] }),
  })
  const cleanup = useMutation({ mutationFn: clearTraces, onSuccess: () => {
    setSelected(null)
    void cache.invalidateQueries({ queryKey: keyRoot })
  } })
  const items = traces.data?.pages.flatMap(page => page.items) ?? []
  const filtered = Boolean(scope || correlationId)
  useEffect(() => {
    const first = traces.data?.pages.flatMap(page => page.items)
    if (!selected && first?.length) setSelected(first.find(item => item.status === 'error') ?? first[0])
  }, [selected, traces.data])
  return <div className="context-inspector">
    <div className="context-inspector-toolbar">
      <div><Text strong>{correlationId ? '本条回复的调用' : scope ? `${scopeNames[scope.kind]}调用` : '全部调用记录'}</Text><div className="context-inspector-caption">查看发送给模型的内容、模型返回和工具执行结果</div></div>
      <Space wrap>
        {filtered && <Button onClick={() => openContextInspector({})}>全部调用</Button>}
        <Select aria-label="记录级别" value={settings.data?.policy.mode === 'full' && settings.data.policy.full_until ? 'timed' : settings.data?.policy.mode}
          loading={settings.isLoading || changeMode.isPending} disabled={!settings.data}
          onChange={value => changeMode.mutate(value)} style={{ minWidth: 145 }}
          options={[{ value: 'off', label: '关闭记录' }, { value: 'summary', label: '仅记录摘要' }, { value: 'timed', label: '完整记录 60 分钟' }, { value: 'full', label: '持续完整记录' }]} />
        <Button aria-label="刷新调用记录" icon={<ReloadOutlined />} onClick={() => void traces.refetch()} />
        <Popconfirm title="清空已结束任务的调用记录？" description="只清理诊断记录，聊天与作品资料保留。" onConfirm={() => cleanup.mutate()}>
          <Button loading={cleanup.isPending}>清空</Button>
        </Popconfirm>
      </Space>
    </div>
    <Alert type="info" showIcon message="记录设置对当前连接中的后续立项、作品助手任务都生效，切换页面不会停止记录。"
      description="无需先创建作品。完整记录会保存提示词、正文与工具结果，仅用于查看，不改变发给模型的上下文。密钥会脱敏；未记录的旧请求无法补回。" />
    {!!(settings.data?.dropped_events || settings.data?.write_errors) && <Alert type="warning" showIcon message="部分诊断内容未能写入，记录可能不完整。正常生成不受此记录状态影响。" />}
    <Failure error={settings.error || changeMode.error || cleanup.error || traces.error} />
    <div className="context-inspector-layout">
      <nav className="context-inspector-traces" aria-label="任务调用记录">
        {traces.isLoading && <Spin />}
        {!traces.isLoading && !items.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={filtered ? '本次筛选暂无记录，可查看全部调用。' : '还没有调用记录。先开启完整记录，再去立项或与助手对话。'} />}
        {items.map(item => <button key={item.id} type="button" className={selected?.id === item.id ? 'trace-selected' : ''} onClick={() => setSelected(item)}>
          <span>{new Date(item.started * 1000).toLocaleString()}</span>
          <Text strong>{scopeNames[item.scope_kind] ?? item.scope_kind}</Text>
          <span><Tag color={item.status === 'error' ? 'red' : 'default'}>{statusNames[item.status] ?? item.status}</Tag><Text type="secondary">{item.mode === 'full' ? '完整内容' : '摘要'}</Text></span>
          <code>{item.id.slice(0, 12)}</code>
          {item.capture_status !== 'recorded' && <Text type="secondary">采集：{statusNames[item.capture_status] ?? item.capture_status}</Text>}
        </button>)}
        {traces.hasNextPage && <Button loading={traces.isFetchingNextPage} onClick={() => void traces.fetchNextPage()}>更早记录</Button>}
      </nav>
      <main className="context-inspector-detail">{selected ? <TraceDetail key={selected.id} trace={selected} /> : <Empty description="选择一次任务查看调用详情" />}</main>
    </div>
  </div>
}

function TraceDetail({ trace }: { trace: ContextTrace }) {
  const [spanId, setSpanId] = useState<string | null>(null)
  const [payload, setPayload] = useState<TraceEvent | null>(null)
  const detail = useQuery({ queryKey: [...keyRoot, trace.id, 'detail'],
    queryFn: ({ signal }) => traceDetail(trace.id, signal), initialData: trace,
    refetchInterval: query => query.state.data?.finished ? false : 2000 })
  const events = useInfiniteQuery({
    queryKey: [...keyRoot, trace.id, 'events'], initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => traceEvents(trace.id, pageParam, signal),
    getNextPageParam: last => last.length === 100 ? last[last.length - 1].sequence : undefined,
    refetchInterval: detail.data.finished ? false : 2000,
  })
  const records = useMemo(() => events.data?.pages.flat() ?? [], [events.data])
  const refetchEvents = events.refetch
  useEffect(() => { if (detail.data.finished != null) void refetchEvents() }, [detail.data.finished, refetchEvents])
  const spans = useMemo(() => buildSpans(records, detail.data, events.hasNextPage === false && !events.isError), [records, detail.data, events.hasNextPage, events.isError])
  const activeId = spanId ?? spans.find(span => span.status === 'error')?.id ?? spans[0]?.id
  const contents = records.filter(event => event.event_type === 'payload' && (event.data.span_id ?? event.span_id) === activeId)
  const download = useMutation({ mutationFn: () => exportTrace(trace.id) })
  return <>
    <div className="context-inspector-toolbar"><Text strong>执行顺序</Text><Popconfirm title="导出这次任务的诊断包？" description="诊断包可能包含创作正文和历史消息。" onConfirm={() => download.mutate()}><Button icon={<DownloadOutlined />} loading={download.isPending}>导出</Button></Popconfirm></div>
    <Failure error={detail.error || events.error || download.error} />
    {events.isLoading && <Spin />}
    <div className="context-inspector-steps" aria-label="执行步骤">
      {spans.map(span => <button type="button" key={span.id} onClick={() => { setSpanId(span.id); setPayload(null) }} className={activeId === span.id ? 'trace-selected' : ''}>
        <span>{span.parent_span_id ? '↳ ' : ''}{({ provider_request: 'API 请求', tool: '工具调用', executor: '执行器', model: '模型步骤', cli: 'CLI 进程', checkpoint: '上下文压缩' } as Record<string, string>)[span.kind ?? ''] ?? '任务'} <code>{span.label}</code></span>
        <span>{span.status_code && `HTTP ${span.status_code} · `}{statusNames[span.status ?? ''] ?? span.status} · {formatTraceDuration(span.duration_ms)}</span>
      </button>)}
    </div>
    {spans.find(span => span.id === activeId)?.usage && <Text type="secondary">提供商实报用量：{JSON.stringify(spans.find(span => span.id === activeId)?.usage)}</Text>}
    {events.hasNextPage && <Button onClick={() => void events.fetchNextPage()} loading={events.isFetchingNextPage}>加载后续步骤</Button>}
    <div className="context-inspector-payloads">
      {contents.map(event => <Button key={event.event_id} type={payload?.event_id === event.event_id ? 'primary' : 'default'} onClick={() => { setSpanId(activeId ?? null); setPayload(event) }}>
        {layerNames[event.data.layer ?? ''] ?? event.data.layer} <Text type="secondary">#{event.sequence}</Text>
      </Button>)}
    </div>
    {payload ? <PayloadDetail key={payload.event_id} traceId={trace.id} event={payload} /> : <Text type="secondary">选择一段输入、输出或回执。正文仅是模型输出，不代表工具已执行。</Text>}
  </>
}

function PayloadDetail({ traceId, event }: { traceId: string; event: TraceEvent }) {
  const [offset, setOffset] = useState(0)
  const result = useQuery({ queryKey: [...keyRoot, traceId, event.event_id, offset],
    queryFn: ({ signal }) => tracePayload(traceId, event.event_id, offset, signal), enabled: !!event.data.content_hash })
  const displayed = useMemo(() => {
    const page = result.data
    if (!page) return ''
    if (offset === 0 && page.next_offset === page.total_characters) {
      try { return JSON.stringify(JSON.parse(page.content), null, 2) } catch { /* A page may contain partial JSON. */ }
    }
    return page.content
  }, [offset, result.data])
  return <section className="context-inspector-content">
    <Space wrap><Tag>{event.data.capture_source === 'http_transport' ? '实际网络交互' : event.data.capture_source === 'cli' || event.data.layer?.startsWith('cli_') ? 'CLI 可见边界，内部 API 不可见' : '应用内部'}</Tag><Tag>{event.data.completeness === 'complete' ? '此层已记录' : event.data.completeness === 'partial' ? '此层不完整' : event.data.completeness === 'not_recorded' ? '未记录正文' : '未知覆盖程度'}</Tag><Text type="secondary">{event.data.stored_bytes == null ? '正文未保存' : `${event.data.stored_bytes} 字节已保存`}</Text></Space>
    {event.data.endpoint && <p><code>{event.data.endpoint}</code></p>}
    {event.data.missing_reason && <Alert type="warning" message={missingNames[event.data.missing_reason] ?? event.data.missing_reason} />}
    <Failure error={result.error} />
    {result.isLoading && !!event.data.content_hash && <Spin />}
    {result.data && <><pre tabIndex={0}>{displayed}</pre><Space>
      <Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 65536))}>上一段</Button>
      <Text type="secondary">{offset + 1}–{result.data.next_offset} / {result.data.total_characters} 字符</Text>
      <Button disabled={result.data.next_offset >= result.data.total_characters} onClick={() => setOffset(result.data.next_offset)}>下一段</Button>
    </Space></>}
    {event.data.usage && <p>提供商实报用量：<code>{JSON.stringify(event.data.usage)}</code></p>}
  </section>
}
