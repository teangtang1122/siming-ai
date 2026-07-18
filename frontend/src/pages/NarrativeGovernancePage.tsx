import { useMemo, useState } from 'react'
import { Alert, Button, Card, Col, Descriptions, Empty, List, Modal, Popconfirm, Row, Segmented, Space, Statistic, Table, Tabs, Tag, Typography, message } from 'antd'
import { CheckCircleOutlined, ClockCircleOutlined, ExclamationCircleOutlined, ReloadOutlined, RollbackOutlined } from '@ant-design/icons'
import {
  type GovernanceItem,
  type NarrativeCheckpoint as Checkpoint,
  getNarrativeCheckpointDiff,
  useNarrativeDashboard,
  useRestoreNarrativeCheckpoint,
  useUpdateNarrativeStatus,
} from '../features/narrativeGovernance'

const { Text, Title } = Typography

const statusColor: Record<string, string> = { open: 'blue', deferred: 'orange', pending_review: 'gold', fulfilled: 'green', resolved: 'green', abandoned: 'default', invalidated: 'red' }

export default function NarrativeGovernancePage({ projectId }: { projectId: string }) {
  const [view, setView] = useState('all')
  const [diff, setDiff] = useState<Record<string, unknown> | null>(null)
  const dashboardQuery = useNarrativeDashboard(projectId, view)
  const statusMutation = useUpdateNarrativeStatus(projectId)
  const restoreMutation = useRestoreNarrativeCheckpoint(projectId)
  const data = dashboardQuery.data
  const loading = dashboardQuery.isLoading || dashboardQuery.isFetching

  const updateStatus = async (type: string, id: string, status: string) => {
    try {
      await statusMutation.mutateAsync({ type, id, status })
      message.success('状态已更新')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '状态更新失败')
    }
  }

  const showDiff = async (checkpoint: Checkpoint) => {
    try {
      setDiff(await getNarrativeCheckpointDiff(projectId, checkpoint.id))
    } catch (error) {
      message.error(error instanceof Error ? error.message : '差异加载失败')
    }
  }

  const restore = async (checkpoint: Checkpoint) => {
    try {
      await restoreMutation.mutateAsync(checkpoint.id)
      message.success('叙事状态已回滚')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '回滚失败')
    }
  }

  const actionButtons = (type: string, item: GovernanceItem) => (
    <Space size={4}>
      {item.status !== 'fulfilled' && item.status !== 'resolved' && <Button size="small" type="text" icon={<CheckCircleOutlined />} title="标记兑现" onClick={() => updateStatus(type, item.id, type === 'causal-edges' ? 'resolved' : 'fulfilled')} />}
      {item.status !== 'deferred' && <Button size="small" type="text" icon={<ClockCircleOutlined />} title="延期" onClick={() => updateStatus(type, item.id, 'deferred')} />}
      {item.status !== 'abandoned' && type !== 'causal-edges' && <Popconfirm title="确认主动放弃这项叙事承诺？" onConfirm={() => updateStatus(type, item.id, 'abandoned')}><Button size="small" type="text" danger icon={<ExclamationCircleOutlined />} title="主动放弃" /></Popconfirm>}
    </Space>
  )

  const promiseColumns = (type: string) => [
    { title: '内容', key: 'title', render: (_: unknown, item: GovernanceItem) => <Space direction="vertical" size={0}><Text strong>{item.title}</Text>{item.description && <Text type="secondary">{item.description}</Text>}</Space> },
    { title: '状态', dataIndex: 'status', width: 110, render: (status: string) => <Tag color={statusColor[status]}>{status}</Tag> },
    { title: '等级', width: 90, render: (_: unknown, item: GovernanceItem) => <Tag>{item.importance || item.priority || 'medium'}</Tag> },
    { title: '目标章', dataIndex: 'target_chapter_number', width: 90, render: (value?: number) => value ? `第 ${value} 章` : '-' },
    { title: '操作', width: 120, render: (_: unknown, item: GovernanceItem) => actionButtons(type, item) },
  ]

  const causalColumns = [
    { title: '原因', dataIndex: 'cause' },
    { title: '结果', dataIndex: 'effect' },
    { title: '强度', dataIndex: 'strength', width: 90, render: (value?: number) => <Tag color={(value || 0) >= 0.75 ? 'red' : 'blue'}>{Math.round((value || 0) * 100)}</Tag> },
    { title: '状态', dataIndex: 'status', width: 100, render: (status: string) => <Tag color={statusColor[status]}>{status}</Tag> },
    { title: '操作', width: 80, render: (_: unknown, item: GovernanceItem) => actionButtons('causal-edges', item) },
  ]

  const metricRows = useMemo(() => (data?.quality_metrics || []).map((item) => ({ ...item, key: String(item.id) })), [data])
  const scoreColumns = ['plot_tension', 'emotional_tension', 'pacing_density', 'character_consistency', 'viewpoint_consistency', 'world_consistency'].map((key) => ({ title: key.replace(/_/g, ' '), dataIndex: key, width: 130, render: (value?: number) => value == null ? '-' : <Tag color={value >= 60 ? 'green' : 'orange'}>{Math.round(value)}</Tag> }))

  return (
    <div style={{ maxWidth: 1500, margin: '0 auto' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <div><Title level={3} style={{ margin: 0 }}>叙事治理</Title><Text type="secondary">伏笔、因果、债务、角色动态、质量与世界线检查点</Text></div>
        <Space><Segmented value={view} onChange={(value) => setView(String(value))} options={[{ label: '全部', value: 'all' }, { label: '即将到期', value: 'due' }, { label: '高风险', value: 'risk' }]} /><Button icon={<ReloadOutlined />} loading={loading} onClick={() => dashboardQuery.refetch()}>刷新</Button></Space>
      </Space>

      <Row gutter={12} style={{ marginBottom: 16 }}>
        <Col span={6}><Card size="small"><Statistic title="开放伏笔" value={data?.counts.open_foreshadowings || 0} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="未闭环因果" value={data?.counts.open_causal_edges || 0} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="叙事债务" value={data?.counts.open_debts || 0} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="检查点" value={data?.checkpoints.length || 0} /></Card></Col>
      </Row>

      {!loading && data && data.foreshadowings.length + data.causal_edges.length + data.narrative_debts.length === 0 && <Alert type="info" showIcon message="当前没有结构化治理项" description="完成作品建档或执行章节写后归档后，伏笔、因果链和叙事债务会出现在这里。" style={{ marginBottom: 16 }} />}

      <Tabs items={[
        { key: 'foreshadowings', label: `伏笔 ${data?.foreshadowings.length || 0}`, children: <Table rowKey="id" loading={loading} size="small" pagination={{ pageSize: 12 }} dataSource={data?.foreshadowings || []} columns={promiseColumns('foreshadowings')} /> },
        { key: 'causal', label: `因果链 ${data?.causal_edges.length || 0}`, children: <Table rowKey="id" loading={loading} size="small" pagination={{ pageSize: 12 }} dataSource={data?.causal_edges || []} columns={causalColumns} /> },
        { key: 'debts', label: `叙事债务 ${data?.narrative_debts.length || 0}`, children: <Table rowKey="id" loading={loading} size="small" pagination={{ pageSize: 12 }} dataSource={data?.narrative_debts || []} columns={promiseColumns('narrative-debts')} /> },
        { key: 'characters', label: `角色动态 ${data?.character_states.length || 0}`, children: <List locale={{ emptyText: <Empty description="暂无角色动态" /> }} dataSource={data?.character_states || []} renderItem={(item) => <List.Item><Descriptions size="small" column={3} style={{ width: '100%' }} items={Object.entries(item).filter(([key, value]) => value && !['id', 'project_id', 'created_at'].includes(key)).slice(0, 9).map(([key, value]) => ({ key, label: key.replace(/_/g, ' '), children: String(value) }))} /></List.Item>} /> },
        { key: 'quality', label: `质量曲线 ${metricRows.length}`, children: <Table rowKey="key" scroll={{ x: 900 }} size="small" dataSource={metricRows} columns={[{ title: '章节', dataIndex: 'chapter_id', width: 140 }, ...scoreColumns]} /> },
        { key: 'checkpoints', label: `世界线 ${data?.checkpoints.length || 0}`, children: <List loading={loading} locale={{ emptyText: <Empty description="暂无叙事检查点" /> }} dataSource={data?.checkpoints || []} renderItem={(item) => <List.Item actions={[<Button key="diff" size="small" onClick={() => showDiff(item)}>查看差异</Button>, <Popconfirm key="restore" title="确认原子回滚触发章节和结构化叙事状态？" onConfirm={() => restore(item)}><Button size="small" danger icon={<RollbackOutlined />}>回滚</Button></Popconfirm>]}><List.Item.Meta title={`#${item.sequence} ${item.label}`} description={`${item.trigger_type}${item.created_at ? ` · ${new Date(item.created_at).toLocaleString()}` : ''}`} /></List.Item>} /> },
      ]} />

      <Modal title="检查点差异" open={Boolean(diff)} onCancel={() => setDiff(null)} footer={<Button onClick={() => setDiff(null)}>关闭</Button>} width={900}>
        <pre style={{ maxHeight: 560, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>{diff ? JSON.stringify(diff, null, 2) : ''}</pre>
      </Modal>
    </div>
  )
}
