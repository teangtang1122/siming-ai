import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import { Badge, Button, Drawer, Empty, Flex, Progress, Space, Spin, Tag, Tooltip, Typography, message } from 'antd'
import { CloseCircleOutlined, ClockCircleOutlined, PauseOutlined, PlayCircleOutlined, ReloadOutlined, UnorderedListOutlined } from '@ant-design/icons'
import { apiClient } from '../api/client'
import { PersistentOutcome } from './interaction'
import type { OperationAttention, OperationOutcome, OperationResult } from './interaction'

const { Paragraph, Text, Title } = Typography

interface ApiResponse<T> { data: T }
interface OperationProgress { mode: 'determinate' | 'indeterminate'; current?: number; total?: number; percent?: number }
interface OperationRun {
  id: string
  source_kind: string
  title: string
  status: string
  outcome?: OperationOutcome
  attention?: OperationAttention
  result?: OperationResult
  result_summary?: string
  health_status: 'active' | 'quiet' | 'suspected_stall' | 'stalled' | 'disconnected'
  phase?: string
  current_message?: string
  progress: OperationProgress
  model_source?: string
  tool_mode?: string
  next_action?: string
  resume_url?: string
  can_pause: boolean
  can_cancel: boolean
  can_retry: boolean
  elapsed_seconds: number
  last_activity_at?: string
}

const ACTIVE_STATUSES = new Set(['queued', 'running', 'waiting_user', 'paused'])
const healthMeta: Record<OperationRun['health_status'], { label: string; color: string }> = {
  active: { label: '正在推进', color: 'success' },
  quiet: { label: '模型仍在计算', color: 'processing' },
  suspected_stall: { label: '疑似停滞', color: 'warning' },
  stalled: { label: '已卡住', color: 'error' },
  disconnected: { label: '运行连接中断', color: 'error' },
}
const statusLabels: Record<string, string> = {
  draft: '草稿', queued: '等待开始', running: '运行中', waiting_user: '等待确认', paused: '已暂停',
  completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断',
}

function elapsedLabel(seconds = 0) {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  if (hours) return `${hours} 小时 ${minutes} 分`
  if (minutes) return `${minutes} 分 ${secs} 秒`
  return `${secs} 秒`
}

function relativeActivity(value?: string) {
  if (!value) return '尚无活动记录'
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000))
  if (seconds < 10) return '刚刚'
  if (seconds < 60) return `${seconds} 秒前`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  return `${Math.floor(seconds / 3600)} 小时前`
}

function OperationItem({ operation, onAction, onOpen }: {
  operation: OperationRun
  onAction: (operation: OperationRun, action: string) => Promise<void>
  onOpen: (operation: OperationRun) => void
}) {
  const active = ACTIVE_STATUSES.has(operation.status)
  const computing = operation.status === 'queued' || operation.status === 'running'
  const health = healthMeta[operation.health_status] || healthMeta.active
  const progress = operation.progress || { mode: 'indeterminate' }
  return (
    <section className="operation-center-item" aria-label={operation.title}>
      <Flex justify="space-between" align="flex-start" gap={12}>
        <div className="operation-center-main">
          <Space size={6} wrap>
            <Text strong>{operation.title}</Text>
            <Tag>{statusLabels[operation.status] || operation.status}</Tag>
            {computing && <Tag color={health.color}>{health.label}</Tag>}
          </Space>
          <Paragraph className="operation-center-message" ellipsis={{ rows: 2, expandable: true, symbol: '展开' }}>
            {operation.current_message || '正在等待新的运行信息'}
          </Paragraph>
        </div>
        {operation.status !== 'waiting_user' && (operation.attention?.action_url || operation.resume_url) && (
          <Button size="small" onClick={() => onOpen(operation)}>
            {operation.attention?.action_label || '查看'}
          </Button>
        )}
      </Flex>

      {computing && progress.mode === 'determinate' && Boolean(progress.total) ? (
        <Progress
          percent={progress.percent || 0}
          size="small"
          format={() => `${progress.current || 0}/${progress.total}`}
          aria-label={`已完成 ${progress.current || 0}，共 ${progress.total}`}
        />
      ) : computing ? (
        <div className="operation-center-indeterminate" aria-live="polite">
          <Spin size="small" />
          <Text type="secondary">正在等待下一条真实活动，不估算完成百分比</Text>
        </div>
      ) : null}

      <div className="operation-center-facts">
        <span><ClockCircleOutlined /> 已运行 {elapsedLabel(operation.elapsed_seconds)}</span>
        <span>最近活动 {relativeActivity(operation.last_activity_at)}</span>
        {operation.phase && <span>阶段 {operation.phase}</span>}
        {operation.model_source && <span>模型 {operation.model_source}</span>}
      </div>
      {operation.outcome && (operation.status === 'waiting_user' || !active) && (
        <PersistentOutcome
          className="operation-center-outcome"
          outcome={operation.outcome}
          attention={operation.attention}
          result={operation.result || { summary: operation.result_summary }}
          onAction={operation.attention?.action_url || operation.resume_url ? () => onOpen(operation) : undefined}
        />
      )}
      {operation.next_action && <Text className="operation-center-next" type="secondary">下一步：{operation.next_action}</Text>}
      {active && (
        <Space size={6} wrap className="operation-center-actions">
          {operation.can_pause && operation.status !== 'paused' && <Button size="small" icon={<PauseOutlined />} onClick={() => void onAction(operation, 'pause')}>暂停</Button>}
          {operation.can_pause && operation.status === 'paused' && <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => void onAction(operation, 'continue')}>继续</Button>}
          {operation.can_retry && operation.health_status !== 'active' && <Button size="small" icon={<ReloadOutlined />} onClick={() => void onAction(operation, 'retry-current-unit')}>重试当前单元</Button>}
          {operation.can_cancel && <Button size="small" danger icon={<CloseCircleOutlined />} onClick={() => void onAction(operation, 'cancel')}>取消</Button>}
        </Space>
      )}
    </section>
  )
}

export default function GlobalOperationCenter() {
  const navigate = useNavigate()
  const location = useLocation()
  const [open, setOpen] = useState(false)
  const [navTarget, setNavTarget] = useState<HTMLElement | null>(null)
  const [operations, setOperations] = useState<OperationRun[]>([])
  const [pollDisconnected, setPollDisconnected] = useState(false)
  const [streamDisconnected, setStreamDisconnected] = useState(false)
  const [actionId, setActionId] = useState<string>()
  const streamRef = useRef<EventSource | null>(null)

  useEffect(() => {
    const attach = () => {
      const target = document.getElementById('global-operation-nav-slot')
      setNavTarget(target)
      return Boolean(target)
    }
    if (attach()) return
    const observer = new MutationObserver(() => {
      if (attach()) observer.disconnect()
    })
    observer.observe(document.body, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [location.pathname])

  const refresh = useCallback(async () => {
    try {
      const response = await apiClient.get<ApiResponse<{ items: OperationRun[] }>>('/operations', { limit: 30 })
      setOperations(response.data.data.items || [])
      setPollDisconnected(false)
    } catch {
      setPollDisconnected(true)
    }
  }, [])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), 3000)
    return () => window.clearInterval(timer)
  }, [refresh])

  const activeOperations = useMemo(() => operations.filter((item) => ACTIVE_STATUSES.has(item.status)), [operations])
  const recentOperations = useMemo(() => operations.filter((item) => !ACTIVE_STATUSES.has(item.status)).slice(0, 10), [operations])
  const primaryActiveId = activeOperations[0]?.id

  useEffect(() => {
    streamRef.current?.close()
    streamRef.current = null
    if (!open || !primaryActiveId) return
    const source = new EventSource(`/api/v1/operations/${primaryActiveId}/stream`)
    streamRef.current = source
    source.onopen = () => setStreamDisconnected(false)
    source.addEventListener('heartbeat', (event) => {
      setStreamDisconnected(false)
      try {
        const next = JSON.parse((event as MessageEvent).data) as OperationRun
        setOperations((current) => current.map((item) => item.id === next.id ? next : item))
      } catch { /* polling remains authoritative */ }
    })
    source.addEventListener('done', () => {
      source.close()
      setStreamDisconnected(false)
      void refresh()
    })
    source.onerror = () => setStreamDisconnected(true)
    return () => source.close()
  }, [open, primaryActiveId, refresh])

  const runAction = useCallback(async (operation: OperationRun, action: string) => {
    setActionId(operation.id)
    try {
      await apiClient.post(`/operations/${operation.id}/${action}`)
      await refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '任务操作失败')
    } finally {
      setActionId(undefined)
    }
  }, [refresh])

  const openResult = useCallback((operation: OperationRun) => {
    const target = operation.attention?.action_url || operation.resume_url
    if (target) navigate(target)
    setOpen(false)
  }, [navigate])

  const trigger = (
    <Tooltip title="查看正在运行和最近完成的任务">
      <Badge
        count={activeOperations.length}
        size="small"
        className={`global-operation-badge${navTarget ? '' : ' global-operation-badge-floating'}`}
      >
        <Button className="global-operation-trigger" icon={<UnorderedListOutlined />} aria-label={`全局任务中心，${activeOperations.length} 个任务正在进行或等待处理`} onClick={() => setOpen(true)}>任务</Button>
      </Badge>
    </Tooltip>
  )

  return (
    <>
      {navTarget ? createPortal(trigger, navTarget) : trigger}
      <Drawer title={<Space><UnorderedListOutlined /><span>任务中心</span></Space>} open={open} onClose={() => setOpen(false)} width={520} className="operation-center-drawer">
        {(pollDisconnected || streamDisconnected) && (
          <div className="operation-center-reconnecting" role="status" aria-live="polite">
            <Spin size="small" />
            <Text>{pollDisconnected ? '正在重新连接司命，后台任务不会因此停止' : '进度流正在重新连接，已改用状态轮询'}</Text>
          </div>
        )}
        <Title level={5}>正在进行</Title>
        {activeOperations.length ? activeOperations.map((operation) => (
          <div key={operation.id} aria-busy={actionId === operation.id}><OperationItem operation={operation} onAction={runAction} onOpen={openResult} /></div>
        )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有正在运行的任务" />}
        <Title level={5} className="operation-center-recent-title">最近任务</Title>
        {recentOperations.length ? recentOperations.map((operation) => <OperationItem key={operation.id} operation={operation} onAction={runAction} onOpen={openResult} />) : <Text type="secondary">尚无任务记录</Text>}
      </Drawer>
    </>
  )
}
