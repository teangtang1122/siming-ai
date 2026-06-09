import { useCallback, useEffect, useState, useRef } from 'react'
import {
  Badge,
  Button,
  Card,
  Collapse,
  Empty,
  List,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ApiOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import type { AgentRun, AgentRunEvent, EventPayload } from '../types/agentRun'

const { Text, Paragraph } = Typography

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface ExternalAgentRunPanelProps {
  projectId: string
}

const STATUS_CONFIG: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  created: { color: 'default', icon: <ClockCircleOutlined />, label: '已创建' },
  running: { color: 'processing', icon: <ClockCircleOutlined spin />, label: '运行中' },
  waiting_confirmation: { color: 'warning', icon: <ExclamationCircleOutlined />, label: '等待确认' },
  completed: { color: 'success', icon: <CheckCircleOutlined />, label: '已完成' },
  failed: { color: 'error', icon: <CloseCircleOutlined />, label: '失败' },
  cancelled: { color: 'default', icon: <StopOutlined />, label: '已取消' },
}

const EVENT_TYPE_LABELS: Record<string, string> = {
  plan: '计划',
  progress: '进度',
  tool_start: '工具调用',
  tool_result: '工具结果',
  context_selected: '上下文',
  draft_chunk: '草稿',
  draft_ready: '草稿完成',
  write_requested: '写入请求',
  write_committed: '写入确认',
  warning: '警告',
  error: '错误',
  run_finished: '运行结束',
}

function parsePayload(event: AgentRunEvent): EventPayload | null {
  if (!event.payload_json) return null
  try {
    return JSON.parse(event.payload_json)
  } catch {
    return null
  }
}

function EventItem({ event }: { event: AgentRunEvent }) {
  const payload = parsePayload(event)
  const typeLabel = EVENT_TYPE_LABELS[event.event_type] || event.event_type
  const isError = event.event_type === 'error' || event.status === 'error'
  const isWarning = event.event_type === 'warning'

  return (
    <List.Item style={{ padding: '8px 0' }}>
      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Space>
          <Tag color={isError ? 'error' : isWarning ? 'warning' : 'blue'}>{typeLabel}</Tag>
          {event.message && (
            <Text type={isError ? 'danger' : isWarning ? 'warning' : undefined}>
              {event.message}
            </Text>
          )}
        </Space>
        {payload?.tool && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            工具: {payload.tool}
            {payload.args_summary && ` — ${payload.args_summary}`}
          </Text>
        )}
        {payload?.plan && (
          <ol style={{ margin: 0, paddingLeft: 20 }}>
            {payload.plan.map((step, i) => (
              <li key={i}><Text>{step}</Text></li>
            ))}
          </ol>
        )}
        {payload?.content && event.event_type === 'draft_chunk' && (
          <Paragraph
            style={{
              maxHeight: 200,
              overflow: 'auto',
              background: '#f5f5f5',
              padding: 8,
              borderRadius: 4,
              fontSize: 13,
              whiteSpace: 'pre-wrap',
            }}
          >
            {payload.content}
          </Paragraph>
        )}
      </Space>
    </List.Item>
  )
}

function ExternalAgentRunPanel({ projectId }: ExternalAgentRunPanelProps) {
  const [runs, setRuns] = useState<AgentRun[]>([])
  const [selectedRun, setSelectedRun] = useState<AgentRun | null>(null)
  const [events, setEvents] = useState<AgentRunEvent[]>([])
  const [loading, setLoading] = useState(false)
  const [panelOpen, setPanelOpen] = useState(false)
  const eventSourceRef = useRef<EventSource | null>(null)

  const fetchRuns = useCallback(async () => {
    try {
      const resp = await apiClient.get<ApiResponse<{ items: AgentRun[] }>>(
        `/projects/${projectId}/agent-runs`
      )
      setRuns(resp.data.data.items)
    } catch {
      // Silent fail for initial load
    }
  }, [projectId])

  const fetchEvents = useCallback(async (runId: string) => {
    try {
      const resp = await apiClient.get<ApiResponse<{ items: AgentRunEvent[] }>>(
        `/projects/${projectId}/agent-runs/${runId}/events`
      )
      setEvents(resp.data.data.items)
    } catch {
      // Silent fail
    }
  }, [projectId])

  useEffect(() => {
    if (panelOpen) {
      fetchRuns()
    }
  }, [panelOpen, fetchRuns])

  useEffect(() => {
    if (selectedRun) {
      fetchEvents(selectedRun.id)
    }
  }, [selectedRun, fetchEvents])

  // SSE subscription for active runs
  useEffect(() => {
    if (!selectedRun || selectedRun.status === 'completed' || selectedRun.status === 'failed' || selectedRun.status === 'cancelled') {
      return
    }

    const es = new EventSource(
      `/api/v1/projects/${projectId}/agent-runs/${selectedRun.id}/stream`
    )
    eventSourceRef.current = es

    es.addEventListener('agent_run_event', (e) => {
      try {
        const event = JSON.parse(e.data) as AgentRunEvent
        setEvents(prev => [...prev, event])
      } catch {
        // Ignore parse errors
      }
    })

    es.onerror = () => {
      es.close()
      eventSourceRef.current = null
      // Refresh run status
      fetchRuns()
    }

    return () => {
      es.close()
      eventSourceRef.current = null
    }
  }, [selectedRun, projectId, fetchRuns])

  const handleCancel = async (runId: string) => {
    setLoading(true)
    try {
      await apiClient.post(`/projects/${projectId}/agent-runs/${runId}/cancel`)
      message.success('已取消')
      fetchRuns()
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '取消失败')
    } finally {
      setLoading(false)
    }
  }

  const activeRun = runs.find(r => r.status === 'running' || r.status === 'waiting_confirmation')

  return (
    <Card
      size="small"
      title={
        <Space>
          <ApiOutlined />
          <span>外部 Agent</span>
          {activeRun && <Badge status="processing" />}
        </Space>
      }
      extra={
        <Button size="small" onClick={() => setPanelOpen(!panelOpen)}>
          {panelOpen ? '收起' : '展开'}
        </Button>
      }
      style={{ marginBottom: 16 }}
    >
      {panelOpen && (
        <>
          {runs.length === 0 ? (
            <Empty
              description="暂无外部 Agent 运行记录"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            >
              <Text type="secondary">
                通过 MCP 连接 Claude Code 或 Codex 后，运行记录将在此显示。
              </Text>
            </Empty>
          ) : (
            <Collapse
              size="small"
              onChange={(key) => {
                const runId = Array.isArray(key) ? key[0] : key
                const run = runs.find(r => r.id === runId)
                if (run) setSelectedRun(run)
              }}
            >
              {runs.map(run => {
                const config = STATUS_CONFIG[run.status] || STATUS_CONFIG.created
                return (
                  <Collapse.Panel
                    key={run.id}
                    header={
                      <Space>
                        <Badge status={config.color as 'success' | 'processing' | 'error' | 'default' | 'warning'} />
                        <Text strong>{run.title || run.client_name || 'Agent Run'}</Text>
                        <Tag>{config.label}</Tag>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {new Date(run.created_at).toLocaleString()}
                        </Text>
                      </Space>
                    }
                    extra={
                      (run.status === 'running' || run.status === 'waiting_confirmation') && (
                        <Button
                          size="small"
                          danger
                          icon={<StopOutlined />}
                          onClick={(e) => {
                            e.stopPropagation()
                            handleCancel(run.id)
                          }}
                          loading={loading}
                        >
                          取消
                        </Button>
                      )
                    }
                  >
                    {run.summary && (
                      <Paragraph style={{ marginBottom: 8 }}>{run.summary}</Paragraph>
                    )}
                    {selectedRun?.id === run.id && events.length > 0 && (
                      <List
                        size="small"
                        dataSource={events}
                        renderItem={event => <EventItem event={event} />}
                        style={{ maxHeight: 400, overflow: 'auto' }}
                      />
                    )}
                    {selectedRun?.id === run.id && events.length === 0 && (
                      <Text type="secondary">暂无事件</Text>
                    )}
                  </Collapse.Panel>
                )
              })}
            </Collapse>
          )}
        </>
      )}
    </Card>
  )
}

export default ExternalAgentRunPanel
