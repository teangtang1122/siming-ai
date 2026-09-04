import { formatApiDateTime } from '../utils/dateTime'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Popconfirm,
  Row,
  Segmented,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd'
import {
  AuditOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  EditOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  RollbackOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import {
  type ChapterGovernanceReview,
  type GovernanceItem,
  type GovernanceStatusPayload,
  type NarrativeCheckpoint as Checkpoint,
  getNarrativeCheckpointDiff,
  useNarrativeDashboard,
  useRestoreNarrativeCheckpoint,
  useUpdateNarrativeStatus,
  useVerifyNarrativeReview,
} from '../features/narrativeGovernance'
import { storeNarrativeSourceLocator } from '../features/narrativeGovernance/sourceLocator'
import './NarrativeGovernancePage.css'

const { Paragraph, Text, Title } = Typography
const { TextArea } = Input

const statusColor: Record<string, string> = {
  open: 'blue',
  deferred: 'orange',
  pending_review: 'gold',
  fulfilled: 'green',
  resolved: 'green',
  abandoned: 'default',
  invalidated: 'red',
  stale: 'volcano',
}
const statusLabel: Record<string, string> = {
  open: '待处理',
  deferred: '已延期',
  pending_review: '待复检',
  fulfilled: '已闭环',
  resolved: '已闭环',
  abandoned: '已放弃',
  invalidated: '已作废',
  stale: '内容已变更',
}
const importanceLabel: Record<string, string> = {
  low: '低', medium: '中', high: '高', critical: '关键',
}
const reviewStatus: Record<string, { label: string; color: string }> = {
  missing: { label: '未检查', color: 'default' },
  needs_review: { label: '待人工复核', color: 'orange' },
  assessed: { label: '已评估', color: 'blue' },
  verified: { label: '已确认', color: 'green' },
  stale: { label: '已失效', color: 'volcano' },
}
const reviewSourceLabel: Record<string, string> = {
  llm: '模型评估', provided: '外部候选', fallback: '规则兜底', manual: '人工检查',
}
const metricLabels: Record<string, string> = {
  plot_tension: '剧情张力',
  emotional_tension: '情绪张力',
  pacing_density: '节奏密度',
  character_consistency: '角色一致性',
  viewpoint_consistency: '视角一致性',
  world_consistency: '世界观一致性',
}
const fieldLabels: Record<string, string> = {
  character_id: '角色', chapter_id: '章节', current_goal: '当前目标', public_stance: '公开立场',
  hidden_intent: '隐藏意图', emotional_residue: '情绪余波', relationship_tension: '关系张力',
  behavior_boundaries: '行为边界', evidence: '正文依据',
}
const triggerLabels: Record<string, string> = {
  chapter_archive: '章节归档', manual: '手动创建', restore: '版本恢复',
  post_write_archive: '写后归档', chapter_create: '章节创建', manual_save: '手动保存',
  pre_restore_safety: '回滚前安全点',
}
const changeLabels: Record<string, string> = {
  foreshadowings: '伏笔', causal_edges: '因果链', narrative_debts: '叙事债务',
  character_states: '角色动态', quality_metrics: '质量记录', chapter_reviews: '覆盖记录',
}

type ActionMode = 'submit_review' | 'verify' | 'defer' | 'abandon' | 'reopen'
interface ActionState { type: string; item: GovernanceItem; mode: ActionMode }
interface ActionValues {
  resolved_chapter_id?: string
  target_chapter_number?: number
  resolution_evidence?: string
  resolution_note?: string
  verification_note?: string
}

interface DiffGroup {
  added?: unknown[]
  removed?: unknown[]
  changed?: unknown[]
}
interface CheckpointDiff {
  checkpoint?: Record<string, unknown>
  chapter_changes?: Record<string, unknown> | null
  changes?: Record<string, DiffGroup>
}

function statusTag(status: string) {
  return <Tag color={statusColor[status] || 'default'}>{statusLabel[status] || status}</Tag>
}

function actionTitle(mode: ActionMode) {
  return {
    submit_review: '提交修复并进入复检',
    verify: '复检并关闭治理项',
    defer: '设置延期目标',
    abandon: '说明放弃或作废原因',
    reopen: '重新打开治理项',
  }[mode]
}

export default function NarrativeGovernancePage({ projectId }: { projectId: string }) {
  const navigate = useNavigate()
  const [view, setView] = useState('all')
  const [diff, setDiff] = useState<CheckpointDiff | null>(null)
  const [action, setAction] = useState<ActionState | null>(null)
  const [review, setReview] = useState<ChapterGovernanceReview | null>(null)
  const [actionForm] = Form.useForm<ActionValues>()
  const [reviewForm] = Form.useForm<{ evidence: string }>()
  const dashboardQuery = useNarrativeDashboard(projectId, view)
  const statusMutation = useUpdateNarrativeStatus(projectId)
  const reviewMutation = useVerifyNarrativeReview(projectId)
  const restoreMutation = useRestoreNarrativeCheckpoint(projectId)
  const data = dashboardQuery.data
  const loading = dashboardQuery.isLoading || dashboardQuery.isFetching
  const chapterReviews = useMemo(() => data?.chapter_reviews || [], [data?.chapter_reviews])
  const chapterOptions = chapterReviews.map((item) => ({
    value: item.chapter_id,
    label: `${item.chapter_title} · v${item.chapter_version}`,
  }))
  const chapterTitle = useMemo(
    () => new Map(chapterReviews.map((item) => [item.chapter_id, item.chapter_title])),
    [chapterReviews],
  )

  const locateChapter = (
    chapterId?: string,
    evidence?: string,
    governanceItemId?: string,
    sourceVersion?: number,
    sourceExcerpt?: string,
    sourceStart?: number,
    sourceEnd?: number,
  ) => {
    if (!chapterId) {
      message.info('该记录尚未绑定来源章节')
      return
    }
    const locatorKey = evidence?.trim() || sourceExcerpt?.trim() || (Number.isInteger(sourceStart) && Number.isInteger(sourceEnd))
      ? storeNarrativeSourceLocator({
          projectId,
          chapterId,
          evidence: evidence || '',
          sourceExcerpt,
          sourceStart,
          sourceEnd,
          governanceItemId,
          sourceVersion,
        })
      : null
    const params = new URLSearchParams({ view: 'writer', chapter: chapterId })
    if (locatorKey) params.set('locate', locatorKey)
    navigate(`/project/${projectId}?${params.toString()}`)
  }

  const openAction = (type: string, item: GovernanceItem, mode: ActionMode) => {
    setAction({ type, item, mode })
    actionForm.setFieldsValue({
      resolved_chapter_id: item.resolved_chapter_id || item.source_chapter_id,
      target_chapter_number: item.target_chapter_number,
      resolution_evidence: item.resolution_evidence,
      resolution_note: item.resolution_note || item.stale_reason,
      verification_note: '',
    })
  }

  const submitAction = async () => {
    if (!action) return
    try {
      const values = await actionForm.validateFields()
      const payload: GovernanceStatusPayload = { status: 'open' }
      if (action.mode === 'submit_review') {
        Object.assign(payload, values, { status: 'pending_review', closed_by: 'user' })
      } else if (action.mode === 'verify') {
        Object.assign(payload, values, {
          status: action.type === 'causal-edges' ? 'resolved' : 'fulfilled',
          closed_by: 'user',
        })
      } else if (action.mode === 'defer') {
        Object.assign(payload, values, { status: 'deferred' })
      } else if (action.mode === 'abandon') {
        Object.assign(payload, values, {
          status: action.type === 'causal-edges' ? 'invalidated' : 'abandoned',
          closed_by: 'user',
        })
      } else {
        Object.assign(payload, values, { status: 'open', closed_by: 'user' })
      }
      await statusMutation.mutateAsync({ type: action.type, id: action.item.id, payload })
      message.success(action.mode === 'verify' ? '复检通过，治理项已闭环' : '治理流程已更新')
      setAction(null)
      actionForm.resetFields()
    } catch (error) {
      if (error instanceof Error) message.error(error.message)
    }
  }

  const submitReviewVerification = async () => {
    if (!review?.id) return
    try {
      const values = await reviewForm.validateFields()
      await reviewMutation.mutateAsync({ reviewId: review.id, evidence: values.evidence })
      message.success('本章治理覆盖已人工确认')
      setReview(null)
      reviewForm.resetFields()
    } catch (error) {
      if (error instanceof Error) message.error(error.message)
    }
  }

  const showDiff = async (checkpoint: Checkpoint) => {
    try {
      setDiff(await getNarrativeCheckpointDiff(projectId, checkpoint.id) as CheckpointDiff)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '差异加载失败')
    }
  }

  const restore = async (checkpoint: Checkpoint) => {
    try {
      await restoreMutation.mutateAsync(checkpoint.id)
      message.success('已回滚；系统同时保留了回滚前安全点')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '回滚失败')
    }
  }

  const actionButtons = (type: string, item: GovernanceItem) => {
    const final = ['fulfilled', 'resolved', 'abandoned', 'invalidated'].includes(item.status)
    return (
      <Space size={4} wrap>
        <Button
          size="small"
          icon={<FileSearchOutlined />}
          onClick={() => {
            const sourceChapter = item.source_chapter_id || item.resolved_chapter_id
            const sourceEvidence = item.source_chapter_id ? item.evidence : item.resolution_evidence
            const sourceVersion = item.source_chapter_id
              ? item.source_chapter_version
              : item.resolved_chapter_version
            locateChapter(
              sourceChapter,
              sourceEvidence,
              item.id,
              sourceVersion,
              item.source_chapter_id ? item.source_excerpt : undefined,
              item.source_chapter_id ? item.source_char_start : undefined,
              item.source_chapter_id ? item.source_char_end : undefined,
            )
          }}
        >
          定位原文
        </Button>
        {item.status === 'pending_review' ? (
          <Button size="small" type="primary" icon={<CheckCircleOutlined />} onClick={() => openAction(type, item, 'verify')}>
            复检关闭
          </Button>
        ) : !final ? (
          <Button size="small" type="primary" icon={<EditOutlined />} onClick={() => openAction(type, item, 'submit_review')}>
            提交修复
          </Button>
        ) : (
          <Button size="small" onClick={() => openAction(type, item, 'reopen')}>重新打开</Button>
        )}
        {!final && type !== 'causal-edges' && item.status !== 'deferred' && (
          <Button size="small" icon={<ClockCircleOutlined />} onClick={() => openAction(type, item, 'defer')}>延期</Button>
        )}
        {!final && (
          <Button size="small" danger onClick={() => openAction(type, item, 'abandon')}>
            {type === 'causal-edges' ? '作废' : '放弃'}
          </Button>
        )}
      </Space>
    )
  }

  const expandedItem = (item: GovernanceItem) => (
    <div className="governance-evidence-panel">
      {item.stale_reason && <Alert type="warning" showIcon message="旧结论已失效" description={item.stale_reason} />}
      <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }} items={[
        { key: 'source', label: '来源章节', children: item.source_chapter_id ? `${chapterTitle.get(item.source_chapter_id) || item.source_chapter_id} · v${item.source_chapter_version || '?'}` : '未绑定' },
        { key: 'resolved', label: '修订章节', children: item.resolved_chapter_id ? `${chapterTitle.get(item.resolved_chapter_id) || item.resolved_chapter_id} · v${item.resolved_chapter_version || '?'}` : '尚未提交修订' },
        { key: 'verified', label: '最后复检', children: item.verified_at ? (formatApiDateTime(item.verified_at) || '时间未记录') : '尚未复检' },
        { key: 'evidence', label: '发现依据', children: item.evidence || '未记录', span: 3 },
        { key: 'resolution_evidence', label: '修复证据', children: item.resolution_evidence || '尚未提交', span: 3 },
        { key: 'resolution', label: '解决说明', children: item.resolution_note || '未记录', span: 3 },
        { key: 'verification', label: '复检结论', children: item.verification_note || '未记录', span: 3 },
      ]} />
      {Boolean(item.recent_events?.length) && (
        <Timeline
          className="governance-history"
          items={item.recent_events?.map((event) => ({
            color: event.to_status === 'stale' ? 'orange' : event.to_status === 'fulfilled' || event.to_status === 'resolved' ? 'green' : 'blue',
            children: (
              <Space direction="vertical" size={0}>
                <Text>{event.from_status ? `${statusLabel[event.from_status] || event.from_status} → ` : ''}{statusLabel[event.to_status] || event.to_status}</Text>
                <Text type="secondary">{event.note || '状态更新'}{event.created_at ? ` · ${(formatApiDateTime(event.created_at) || '时间未记录')}` : ''}</Text>
              </Space>
            ),
          }))}
        />
      )}
    </div>
  )

  const promiseColumns = (type: string) => [
    {
      title: '治理事项', key: 'title', render: (_: unknown, item: GovernanceItem) => (
        <Space direction="vertical" size={2}>
          <Text strong>{item.title || '未命名治理项'}</Text>
          {item.description && <Text type="secondary" ellipsis={{ tooltip: item.description }}>{item.description}</Text>}
          {item.stale_reason && <Text type="warning">关联正文已变化，需要重新修复</Text>}
        </Space>
      ),
    },
    { title: '状态', dataIndex: 'status', width: 110, render: statusTag },
    { title: '等级', width: 80, render: (_: unknown, item: GovernanceItem) => <Tag>{importanceLabel[item.importance || item.priority || 'medium'] || '未分级'}</Tag> },
    { title: '目标章', dataIndex: 'target_chapter_number', width: 88, render: (value?: number) => value ? `第 ${value} 章` : '—' },
    { title: '下一步', width: 330, render: (_: unknown, item: GovernanceItem) => actionButtons(type, item) },
  ]

  const causalColumns = [
    { title: '原因', dataIndex: 'cause' },
    { title: '结果', dataIndex: 'effect' },
    { title: '强度', dataIndex: 'strength', width: 78, render: (value?: number) => <Tag color={(value || 0) >= 0.75 ? 'red' : 'blue'}>{Math.round((value || 0) * 100)}</Tag> },
    { title: '状态', dataIndex: 'status', width: 110, render: statusTag },
    { title: '下一步', width: 330, render: (_: unknown, item: GovernanceItem) => actionButtons('causal-edges', item) },
  ]

  const coverageColumns = [
    { title: '章节', dataIndex: 'chapter_title', render: (value: string, item: ChapterGovernanceReview) => <Space direction="vertical" size={0}><Text strong>{value}</Text><Text type="secondary">正文 v{item.chapter_version}</Text></Space> },
    { title: '覆盖状态', dataIndex: 'status', width: 130, render: (value: string) => <Tag color={reviewStatus[value]?.color}>{reviewStatus[value]?.label || value}</Tag> },
    { title: '检查来源', dataIndex: 'source', width: 110, render: (value?: string) => value ? reviewSourceLabel[value] || value : '—' },
    { title: '发现', dataIndex: 'findings_count', width: 75, render: (value: number) => `${value || 0} 条` },
    { title: '复核说明', dataIndex: 'evidence', ellipsis: true, render: (value?: string) => value || '尚未留下检查证据' },
    {
      title: '操作', width: 250, render: (_: unknown, item: ChapterGovernanceReview) => (
        <Space wrap>
          <Button size="small" icon={<FileSearchOutlined />} onClick={() => locateChapter(item.chapter_id)}>定位章节</Button>
          {item.id && item.status !== 'verified' && item.status !== 'missing' && (
            <Button size="small" type="primary" icon={<AuditOutlined />} onClick={() => { setReview(item); reviewForm.setFieldsValue({ evidence: item.evidence || '' }) }}>
              人工确认
            </Button>
          )}
          {['missing', 'stale'].includes(item.status) && <Button size="small" onClick={() => navigate(`/project/${projectId}?view=cataloging`)}>重新检查</Button>}
        </Space>
      ),
    },
  ]

  const metricRows = useMemo(() => (data?.quality_metrics || []).map((item) => ({ ...item, key: String(item.id) })), [data])
  const scoreColumns = Object.keys(metricLabels).map((key) => ({
    title: metricLabels[key], dataIndex: key, width: 118,
    render: (value?: number) => value == null ? '—' : <Tag color={value >= 60 ? 'green' : 'orange'}>{Math.round(value)}</Tag>,
  }))
  const itemCount = (data?.foreshadowings.length || 0) + (data?.causal_edges.length || 0) + (data?.narrative_debts.length || 0)
  const isEmpty = !loading && Boolean(data) && itemCount === 0 && chapterReviews.length === 0 && (data?.checkpoints.length || 0) === 0
  const coverage = data?.coverage || { total_chapters: 0, assessed_chapters: 0, verified_chapters: 0, gaps: 0 }

  const diffSummary = Object.entries(diff?.changes || {}).map(([key, group]) => ({
    key,
    label: changeLabels[key] || key,
    added: group.added?.length || 0,
    removed: group.removed?.length || 0,
    changed: group.changed?.length || 0,
  })).filter((item) => item.added + item.removed + item.changed > 0)

  return (
    <div className="narrative-governance-page">
      <div className="narrative-governance-head">
        <div>
          <Space align="center"><SafetyCertificateOutlined className="narrative-title-icon" /><Title level={3}>叙事治理</Title></Space>
          <Paragraph type="secondary">每条问题都要经过定位、修订和复检；正文一旦变化，旧结论会自动失效。</Paragraph>
        </div>
        <Space wrap>
          <Segmented value={view} onChange={(value) => setView(String(value))} options={[{ label: '全部', value: 'all' }, { label: '即将到期', value: 'due' }, { label: '高风险', value: 'risk' }]} />
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => dashboardQuery.refetch()}>刷新</Button>
        </Space>
      </div>

      {coverage.gaps > 0 && (
        <Alert
          className="governance-attention"
          type="warning"
          showIcon
          message={`${coverage.gaps} 个章节版本尚未形成可信治理覆盖`}
          description="未检查、规则兜底待复核或因正文改动失效的章节，不会再被当作“没有问题”。"
          action={<Button onClick={() => navigate(`/project/${projectId}?view=cataloging`)}>处理覆盖缺口</Button>}
        />
      )}

      {isEmpty ? (
        <Card className="narrative-empty-card">
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<div className="narrative-empty-copy"><Text strong>还没有可治理的叙事记录</Text><Text type="secondary">完成章节归档或作品建档后，系统会记录每章是否真正执行过治理检查。</Text></div>}>
            <Space wrap><Button type="primary" onClick={() => navigate(`/project/${projectId}?view=cataloging`)}>前往作品建档</Button><Button onClick={() => navigate(`/project/${projectId}`)}>返回写作</Button></Space>
          </Empty>
        </Card>
      ) : (
        <>
          <Row gutter={[12, 12]} className="narrative-metrics">
            <Col xs={24} sm={12} lg={6}><Card size="small"><Statistic title="未闭环事项" value={(data?.counts.open_foreshadowings || 0) + (data?.counts.open_causal_edges || 0) + (data?.counts.open_debts || 0)} /></Card></Col>
            <Col xs={24} sm={12} lg={6}><Card size="small"><Statistic title="等待复检" value={data?.counts.pending_review || 0} valueStyle={{ color: 'var(--ant-color-warning)' }} /></Card></Col>
            <Col xs={24} sm={12} lg={6}><Card size="small"><Statistic title="已因改稿失效" value={data?.counts.stale || 0} valueStyle={{ color: 'var(--ant-color-error)' }} /></Card></Col>
            <Col xs={24} sm={12} lg={6}><Card size="small"><Statistic title="治理覆盖" value={coverage.assessed_chapters} suffix={`/ ${coverage.total_chapters}`} /></Card></Col>
          </Row>

          <Tabs items={[
            { key: 'foreshadowings', label: `伏笔 ${data?.foreshadowings.length || 0}`, children: <Table rowKey="id" loading={loading} size="small" pagination={{ pageSize: 12 }} dataSource={data?.foreshadowings || []} columns={promiseColumns('foreshadowings')} expandable={{ expandedRowRender: expandedItem }} /> },
            { key: 'causal', label: `因果链 ${data?.causal_edges.length || 0}`, children: <Table rowKey="id" loading={loading} size="small" pagination={{ pageSize: 12 }} dataSource={data?.causal_edges || []} columns={causalColumns} expandable={{ expandedRowRender: expandedItem }} /> },
            { key: 'debts', label: `叙事债务 ${data?.narrative_debts.length || 0}`, children: <Table rowKey="id" loading={loading} size="small" pagination={{ pageSize: 12 }} dataSource={data?.narrative_debts || []} columns={promiseColumns('narrative-debts')} expandable={{ expandedRowRender: expandedItem }} /> },
            { key: 'coverage', label: `治理覆盖 ${coverage.assessed_chapters}/${coverage.total_chapters}`, children: <Table rowKey={(item) => `${item.chapter_id}:${item.chapter_version}`} loading={loading} size="small" pagination={{ pageSize: 15 }} dataSource={chapterReviews} columns={coverageColumns} /> },
            { key: 'characters', label: `角色动态 ${data?.character_states.length || 0}`, children: <List locale={{ emptyText: <Empty description="暂无角色动态" /> }} dataSource={data?.character_states || []} renderItem={(item) => <List.Item><Descriptions size="small" column={3} style={{ width: '100%' }} items={Object.entries(item).filter(([key, value]) => value && !['id', 'project_id', 'created_at'].includes(key)).slice(0, 9).map(([key, value]) => ({ key, label: fieldLabels[key] || key, children: String(value) }))} /></List.Item>} /> },
            { key: 'quality', label: `质量曲线 ${metricRows.length}`, children: <Table rowKey="key" scroll={{ x: 980 }} size="small" dataSource={metricRows} columns={[{ title: '章节', dataIndex: 'chapter_id', width: 160, render: (value: string, item?: Record<string, unknown>) => <Space direction="vertical" size={0}><Text>{chapterTitle.get(value) || value}</Text><Text type="secondary">v{String(item?.chapter_version || '?')}</Text></Space> }, { title: '总分', dataIndex: 'total_score', width: 88, render: (value?: number, item?: Record<string, unknown>) => value == null ? '—' : `${Math.round(value)} / ${String(item?.max_score || 80)}` }, ...scoreColumns]} /> },
            { key: 'checkpoints', label: `世界线 ${data?.checkpoints.length || 0}`, children: <List loading={loading} locale={{ emptyText: <Empty description="暂无叙事检查点" /> }} dataSource={data?.checkpoints || []} renderItem={(item) => <List.Item actions={[<Button key="diff" size="small" onClick={() => showDiff(item)}>查看影响</Button>, <Popconfirm key="restore" title="回滚章节与结构化叙事状态？" description="系统会先自动创建回滚前安全点，仍建议确认目标检查点无误。" okText="确认回滚" cancelText="取消" onConfirm={() => restore(item)}><Button size="small" danger icon={<RollbackOutlined />}>回滚</Button></Popconfirm>]}><List.Item.Meta title={<Space><Text strong>#{item.sequence} {item.label}</Text>{item.review_summary && <Tag color="blue">含治理覆盖</Tag>}</Space>} description={`${triggerLabels[item.trigger_type] || '叙事更新'}${item.created_at ? ` · ${(formatApiDateTime(item.created_at) || '时间未记录')}` : ''}`} /></List.Item>} /> },
          ]} />
        </>
      )}

      <Modal title={action ? actionTitle(action.mode) : ''} open={Boolean(action)} onCancel={() => setAction(null)} onOk={submitAction} okText={action?.mode === 'verify' ? '确认闭环' : '保存'} confirmLoading={statusMutation.isPending} destroyOnHidden>
        {action && <Form form={actionForm} layout="vertical" preserve={false}>
          {(action.mode === 'submit_review' || action.mode === 'verify') && <Form.Item name="resolved_chapter_id" label="实际修订章节" rules={[{ required: true, message: '请选择实际修改过的章节' }]}><Select showSearch optionFilterProp="label" options={chapterOptions} placeholder="选择承载修复内容的章节" /></Form.Item>}
          {action.mode === 'defer' && <Form.Item name="target_chapter_number" label="计划处理章节" rules={[{ required: true, message: '请填写计划处理章节' }]}><Input type="number" min={1} /></Form.Item>}
          {(action.mode === 'submit_review' || action.mode === 'abandon' || action.mode === 'reopen') && <Form.Item name="resolution_note" label={action.mode === 'submit_review' ? '解决说明' : '操作原因'} rules={[{ required: true, min: 4, message: '请至少填写 4 个字符' }]}><TextArea rows={4} placeholder="说明修改了什么、为什么足以解决问题" maxLength={4000} showCount /></Form.Item>}
          {action.mode === 'submit_review' && <Form.Item name="resolution_evidence" label="正文证据（建议填写）"><TextArea rows={3} placeholder="可粘贴关键句、位置或验证依据" maxLength={4000} showCount /></Form.Item>}
          {action.mode === 'verify' && <><Form.Item name="resolution_note" hidden><Input /></Form.Item><Form.Item name="resolution_evidence" hidden><Input /></Form.Item><Form.Item name="verification_note" label="复检结论" rules={[{ required: true, min: 4, message: '请至少填写 4 个字符' }]}><TextArea rows={4} placeholder="复读修订章节后，说明为何可以关闭此项" maxLength={4000} showCount /></Form.Item></>}
        </Form>}
      </Modal>

      <Modal title="确认本章治理覆盖" open={Boolean(review)} onCancel={() => setReview(null)} onOk={submitReviewVerification} okText="确认已检查" confirmLoading={reviewMutation.isPending} destroyOnHidden>
        {review && <><Alert type="info" showIcon message={`${review.chapter_title} · v${review.chapter_version}`} description="确认后只对当前正文版本有效；后续改稿会自动使本结论失效。" /><Form form={reviewForm} layout="vertical" preserve={false} className="governance-review-form"><Form.Item name="evidence" label="人工复核说明" rules={[{ required: true, min: 4, message: '请至少填写 4 个字符' }]}><TextArea rows={4} maxLength={4000} showCount placeholder="例如：已核对本章伏笔、因果与未完成行动，未发现遗漏。" /></Form.Item></Form></>}
      </Modal>

      <Modal title="检查点影响" open={Boolean(diff)} onCancel={() => setDiff(null)} footer={<Button onClick={() => setDiff(null)}>关闭</Button>} width={820}>
        {diff && <Space direction="vertical" size="large" style={{ width: '100%' }}>
          {diff.chapter_changes ? <Alert type="warning" showIcon message="该操作还会恢复关联章节正文" description="章节标题、正文或版本与检查点相比已有变化。" /> : <Alert type="info" showIcon message="关联章节正文没有可见变化" />}
          {diffSummary.length ? <List dataSource={diffSummary} renderItem={(item) => <List.Item><List.Item.Meta title={item.label} description={<Space wrap><Tag color="green">新增 {item.added}</Tag><Tag color="red">移除 {item.removed}</Tag><Tag color="blue">修改 {item.changed}</Tag></Space>} /></List.Item>} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="结构化叙事状态与该检查点一致" />}
          <Alert type="success" showIcon message="回滚前会自动创建安全点" description="即使回滚结果不符合预期，也可以从新生成的安全点恢复。" />
        </Space>}
      </Modal>
    </div>
  )
}
