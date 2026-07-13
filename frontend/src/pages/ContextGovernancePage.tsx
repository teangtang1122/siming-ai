import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Drawer,
  Empty,
  Input,
  List,
  Modal,
  Progress,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  EyeOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'

const { Text, Title } = Typography

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface ManifestItem {
  id: string
  category: string
  source_type: string
  source_id?: string | null
  chunk_id?: string | null
  source_hash?: string | null
  title: string
  required: boolean
  pinned: boolean
  estimated_tokens: number
  selection_reason: string
  evidence_submitted_at?: string | null
  scores: { lexical?: number | null; semantic?: number | null; recency?: number | null; structural?: number | null; final?: number | null }
  content?: string
}

interface ContextManifest {
  id: string
  task_type: string
  model?: string | null
  provider?: string | null
  execution_route: string
  status: string
  warnings: string[]
  coverage: Record<string, { required?: boolean; status?: string; item_count?: number; reason?: string }>
  budget: {
    context_window_tokens: number
    input_budget_tokens: number
    output_reserve_tokens: number
    safety_margin_tokens: number
    estimated_input_tokens: number
    estimated_input_chars: number
    remaining_input_tokens: number
  }
  override?: { reason?: string | null; actor?: string | null; at?: string | null }
  stale_reason?: string | null
  items: ManifestItem[]
  created_at?: string | null
}

interface ProjectContextStatus {
  generation_allowed: boolean
  reason?: string
  semantic?: { available?: boolean; model?: string; reason?: string }
}

interface RebuildProject {
  project_id: string
  status: string
  indexed_chunks: number
  semantic_chunks: number
  error?: string | null
}

interface RebuildJob {
  id: string
  status: string
  total_projects: number
  completed_projects: number
  failed_projects: number
  projects: RebuildProject[]
}

const STATUS_COLOR: Record<string, string> = {
  ready: 'green',
  overridden: 'gold',
  needs_confirmation: 'orange',
  stale: 'red',
  blocked_rebuild: 'volcano',
  queued: 'blue',
  running: 'processing',
  completed: 'green',
  failed: 'red',
}

const STATUS_LABEL: Record<string, string> = {
  ready: '可执行',
  overridden: '已覆写',
  needs_confirmation: '待确认',
  stale: '已失效',
  blocked_rebuild: '索引维护中',
  queued: '排队中',
  running: '重建中',
  completed: '已完成',
  failed: '失败',
}

const tokenPercent = (manifest: ContextManifest) => {
  const total = Math.max(1, manifest.budget.input_budget_tokens)
  return Math.min(100, Math.round((manifest.budget.estimated_input_tokens / total) * 100))
}

export default function ContextGovernancePage({ projectId }: { projectId: string }) {
  const [manifests, setManifests] = useState<ContextManifest[]>([])
  const [status, setStatus] = useState<ProjectContextStatus | null>(null)
  const [rebuild, setRebuild] = useState<RebuildJob | null>(null)
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<ContextManifest | null>(null)
  const [overrideTarget, setOverrideTarget] = useState<ContextManifest | null>(null)
  const [overrideReason, setOverrideReason] = useState('')
  const [submittingOverride, setSubmittingOverride] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [statusResponse, manifestsResponse, rebuildResponse] = await Promise.all([
        apiClient.get<ApiResponse<ProjectContextStatus>>(`/projects/${projectId}/context-governance-status`),
        apiClient.get<ApiResponse<{ items: ContextManifest[] }>>(`/projects/${projectId}/context-manifests`),
        apiClient.get<ApiResponse<{ items: RebuildJob[] }>>('/context-governance/rebuilds', { limit: 12 }),
      ])
      setStatus(statusResponse.data.data)
      setManifests(manifestsResponse.data.data.items || [])
      const relevant = (rebuildResponse.data.data.items || []).find((job) => job.projects.some((item) => item.project_id === projectId)) || null
      setRebuild(relevant)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载上下文治理状态失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => { load() }, [load])

  const openManifest = async (manifest: ContextManifest) => {
    try {
      const response = await apiClient.get<ApiResponse<ContextManifest>>(`/projects/${projectId}/context-manifests/${manifest.id}`)
      setSelected(response.data.data)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载 Manifest 失败')
    }
  }

  const rebuildProject = async () => {
    try {
      await apiClient.post('/context-governance/rebuilds', { project_ids: [projectId], requested_by: 'author' })
      message.success('上下文索引重建已开始')
      load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '无法启动索引重建')
    }
  }

  const submitOverride = async () => {
    if (!overrideTarget || !overrideReason.trim()) return
    setSubmittingOverride(true)
    try {
      await apiClient.post(`/projects/${projectId}/context-manifests/${overrideTarget.id}/override`, {
        reason: overrideReason.trim(),
        actor: 'author',
      })
      message.success('已记录上下文覆写原因')
      setOverrideTarget(null)
      setOverrideReason('')
      load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '覆写未保存')
    } finally {
      setSubmittingOverride(false)
    }
  }

  const currentRebuild = useMemo(
    () => rebuild?.projects.find((item) => item.project_id === projectId) || null,
    [projectId, rebuild],
  )

  const columns = [
    {
      title: '状态',
      dataIndex: 'status',
      width: 118,
      render: (value: string) => <Tag color={STATUS_COLOR[value] || 'default'}>{STATUS_LABEL[value] || value}</Tag>,
    },
    {
      title: '任务',
      dataIndex: 'task_type',
      width: 126,
      render: (value: string, item: ContextManifest) => <Space direction="vertical" size={0}><Text strong>{value}</Text><Text type="secondary" style={{ fontSize: 11 }}>{item.execution_route}</Text></Space>,
    },
    {
      title: '输入预算',
      key: 'budget',
      width: 220,
      render: (_: unknown, item: ContextManifest) => (
        <div style={{ minWidth: 170 }}>
          <Progress percent={tokenPercent(item)} size="small" status={item.status === 'stale' ? 'exception' : undefined} showInfo={false} />
          <Text type="secondary" style={{ fontSize: 11 }}>{item.budget.estimated_input_tokens} / {item.budget.input_budget_tokens} tokens</Text>
        </div>
      ),
    },
    {
      title: '模型',
      key: 'model',
      render: (_: unknown, item: ContextManifest) => <Text ellipsis={{ tooltip: item.model || '未知模型' }} style={{ maxWidth: 180 }}>{item.model || '未知模型 (16K 回退)'}</Text>,
    },
    {
      title: '操作',
      width: 120,
      render: (_: unknown, item: ContextManifest) => (
        <Space size={2}>
          <Button type="text" icon={<EyeOutlined />} title="查看来源与预算" onClick={() => openManifest(item)} />
          {item.status === 'needs_confirmation' && <Button type="text" icon={<SafetyCertificateOutlined />} title="记录覆写原因" onClick={() => setOverrideTarget(item)} />}
        </Space>
      ),
    },
  ]

  return (
    <div style={{ maxWidth: 1500, margin: '0 auto' }}>
      <Space align="start" style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>上下文治理</Title>
          <Text type="secondary">每次生成、评审和外部 Agent 执行使用的来源、预算与确认记录。</Text>
        </div>
        <Space>
          <Button icon={<SyncOutlined />} loading={loading} onClick={rebuildProject}>重建索引</Button>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>
        </Space>
      </Space>

      {status && !status.generation_allowed && (
        <Alert
          type="warning"
          showIcon
          message="该作品的生成与正式写入暂时受限"
          description={status.reason || '上下文索引尚未达到当前版本。浏览、编辑与检索仍可使用。'}
          style={{ marginBottom: 16 }}
        />
      )}

      {currentRebuild && currentRebuild.status !== 'completed' && (
        <Alert
          type={currentRebuild.status === 'failed' ? 'error' : 'info'}
          showIcon
          message={`索引${STATUS_LABEL[currentRebuild.status] || currentRebuild.status}`}
          description={`词法块 ${currentRebuild.indexed_chunks}，语义块 ${currentRebuild.semantic_chunks}${currentRebuild.error ? `。${currentRebuild.error}` : ''}`}
          style={{ marginBottom: 16 }}
        />
      )}

      {status?.semantic && (
        <div style={{ marginBottom: 14, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <Tag color={status.semantic.available ? 'green' : 'default'}>{status.semantic.available ? '本地语义检索可用' : '词法检索模式'}</Tag>
          <Text type="secondary" style={{ fontSize: 12 }}>{status.semantic.model}{status.semantic.reason ? ` · ${status.semantic.reason}` : ''}</Text>
        </div>
      )}

      <Table
        rowKey="id"
        loading={loading}
        size="small"
        dataSource={manifests}
        columns={columns}
        pagination={{ pageSize: 12, hideOnSinglePage: true }}
        locale={{ emptyText: <Empty description="尚无上下文 Manifest" /> }}
        scroll={{ x: 820 }}
      />

      <Drawer
        title={selected ? `Manifest · ${selected.task_type}` : 'Manifest'}
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        width={Math.min(760, window.innerWidth - 36)}
      >
        {selected && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Alert
              type={selected.status === 'ready' || selected.status === 'overridden' ? 'success' : 'warning'}
              showIcon
              message={STATUS_LABEL[selected.status] || selected.status}
              description={selected.stale_reason || selected.override?.reason || selected.warnings?.[0] || '所有选择均可审计。'}
            />
            <div>
              <Text strong>上下文窗口</Text>
              <Progress percent={tokenPercent(selected)} status={selected.status === 'stale' ? 'exception' : undefined} />
              <Text type="secondary">输入 {selected.budget.estimated_input_tokens} / {selected.budget.input_budget_tokens} · 输出预留 {selected.budget.output_reserve_tokens} · 安全余量 {selected.budget.safety_margin_tokens}</Text>
            </div>
            <div>
              <Text strong>必需项覆盖</Text>
              <List
                size="small"
                dataSource={Object.entries(selected.coverage || {})}
                renderItem={([name, item]) => (
                  <List.Item>
                    <Space direction="vertical" size={0} style={{ width: '100%' }}>
                      <Space><Tag color={item.status === 'covered' || item.status === 'not_applicable' ? 'green' : 'orange'}>{item.status || 'unknown'}</Tag><Text>{name}</Text>{item.required && <Tag>必需</Tag>}</Space>
                      {item.reason && <Text type="secondary" style={{ fontSize: 12 }}>{item.reason}</Text>}
                    </Space>
                  </List.Item>
                )}
              />
            </div>
            <div>
              <Text strong>选取来源</Text>
              <List
                size="small"
                dataSource={selected.items || []}
                renderItem={(item) => (
                  <List.Item>
                    <Space direction="vertical" size={3} style={{ width: '100%' }}>
                      <Space wrap><Tag>{item.category}</Tag>{item.required && <Tag color="red">必需</Tag>}{item.pinned && <Tag color="gold">固定</Tag>}<Text strong>{item.title}</Text></Space>
                      <Text type="secondary" style={{ fontSize: 12 }}>{item.source_type}:{item.source_id || item.chunk_id || 'inline'} · {item.estimated_tokens} tokens</Text>
                      <Text style={{ fontSize: 12 }}>{item.selection_reason}</Text>
                      <Text type="secondary" ellipsis={{ tooltip: item.source_hash }} style={{ fontSize: 11 }}>hash: {item.source_hash || 'n/a'}</Text>
                    </Space>
                  </List.Item>
                )}
              />
            </div>
          </Space>
        )}
      </Drawer>

      <Modal
        title="覆写上下文契约"
        open={Boolean(overrideTarget)}
        okText="确认覆写"
        okButtonProps={{ disabled: !overrideReason.trim(), loading: submittingOverride }}
        onOk={submitOverride}
        onCancel={() => { setOverrideTarget(null); setOverrideReason('') }}
      >
        <Text type="secondary">覆写会保留在审计记录中，并不会掩盖之后发生的来源失效。</Text>
        <Input.TextArea
          autoFocus
          rows={4}
          value={overrideReason}
          onChange={(event) => setOverrideReason(event.target.value)}
          placeholder="说明为何可以在缺少该上下文时继续"
          style={{ marginTop: 12 }}
        />
      </Modal>
    </div>
  )
}
