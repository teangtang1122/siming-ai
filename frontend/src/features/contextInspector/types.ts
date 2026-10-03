import type { TraceScope } from '../../shared/contextInspector'
export type { TraceScope } from '../../shared/contextInspector'

export interface ContextTrace {
  id: string
  cursor: number
  scope_kind: TraceScope['kind']
  scope_id: string
  started: number
  finished: number | null
  status: string
  mode: 'off' | 'summary' | 'full'
  capture_status: string
  dropped: number
  correlations: Record<string, string | null>
}

export interface TraceEvent {
  event_id: string
  trace_id: string
  source_id: string
  sequence: number
  timestamp: number
  event_type: string
  span_id: string | null
  data: {
    span_id?: string
    parent_span_id?: string | null
    kind?: string
    label?: string
    status?: string
    duration_ms?: number
    layer?: string
    endpoint?: string
    status_code?: number
    media_type?: string
    capture_source?: string
    completeness?: string
    missing_reason?: string | null
    content_hash?: string
    stored_bytes?: number
    usage?: Record<string, unknown> | null
  }
}

export interface TraceHealth {
  policy: { mode: 'off' | 'summary' | 'full'; full_until: number | null }
  dropped_events: number
  write_errors: number
  pending_bytes: number
}

export const layerNames: Record<string, string> = {
  context_frame: '上下文帧', logical_request: '应用准备的请求', provider_request: '实际 API 请求',
  provider_response: '提供商返回', adapter_output: '适配后输出', tool_arguments: '工具参数',
  tool_receipt: '执行器回执', model_visible_tool_result: '送回模型的结果', cli_input: 'CLI 输入', cli_event: 'CLI 事件',
}

export const missingNames: Record<string, string> = {
  recording_not_enabled: '当时未开启完整内容记录', size_limit: '超过采集大小上限',
  size_or_encoding_limit: '超过大小上限或无法编码', unparseable_body: '无法安全解析，未保存原始片段',
  unsupported_encoding: '当前无法解码此压缩格式', incomplete_encoding: '压缩内容未完整结束',
  decoding_failed: '响应解码失败', request_interrupted: '请求发送中断',
  stream_interrupted: '调用方在读到流末尾之前结束读取；仅展示已安全解析的内容',
  capture_busy: '采集处理繁忙，已跳过这段正文',
}

export const statusNames: Record<string, string> = {
  running: '运行中', completed: '已完成', error: '失败', cancelled: '已取消',
  superseded: '已被新回合替换', partial: '部分记录', interrupted: '采集中断',
  recording: '记录中', recorded: '已记录',
  incomplete: '缺少结束记录', pending_record: '待加载后续记录',
}

export function formatTraceDuration(duration?: number) {
  return duration == null ? '耗时未知' : duration < 1 ? '<1 ms' : `${Math.round(duration)} ms`
}

export function buildSpans(events: TraceEvent[], trace?: Pick<ContextTrace, 'finished' | 'capture_status'>, complete = true) {
  const spans = new Map<string, TraceEvent['data'] & { id: string; sequence: number }>()
  events.forEach(event => {
    const id = event.data.span_id ?? event.span_id
    if (!id || !['span_started', 'span_finished', 'http_response', 'usage'].includes(event.event_type)) return
    spans.set(id, { id, sequence: event.sequence, ...spans.get(id), ...event.data })
  })
  return [...spans.values()].map(span => {
    if (trace?.finished != null && span.status === 'running') {
      return { ...span, status: !complete ? 'pending_record' : trace.capture_status === 'interrupted' ? 'interrupted' : 'incomplete' }
    }
    return span
  }).sort((a, b) => a.sequence - b.sequence)
}
