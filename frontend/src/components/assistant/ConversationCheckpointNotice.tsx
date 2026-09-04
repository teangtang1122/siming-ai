import { Alert, Button, Descriptions, Divider, Empty, List, Modal, Space, Tag, Typography } from 'antd'
import { HistoryOutlined, InfoCircleOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons'
import type {
  ConversationCheckpointDetail,
  ConversationCheckpointSemanticNavigation,
  ConversationContextState,
} from '../../types/conversationContext'
import { formatApiDateTime } from '../../utils/dateTime'
import { checkpointIdForState } from '../../services/conversationContext'

const { Paragraph, Text } = Typography

const STATUS_LABELS: Record<string, string> = {
  pending: '等待整理',
  compressing: '正在整理',
  ready: '可用',
  failed: '失败',
  cancelled: '已取消',
  superseded: '已被替代',
}

const TRIGGER_LABELS: Record<string, string> = {
  projected_next_step_over_capacity: '下一模型步骤预计会超过当前模型的上下文容量',
  active_history_over_capacity: '活动对话历史超过当前模型的上下文容量',
  tool_schema_growth_over_capacity: '开放新工具后，请求预计超过当前模型容量',
  model_window_changed: '切换后的模型窗口更小，需要重新规划活动上下文',
  manual_rebuild: '作者主动要求重新整理',
}

const ASSURANCE_LABELS: Record<string, string> = {
  exact: '精确计数',
  conservative: '保守上界',
  unverified: '未验证容量（已启用安全兜底窗口）',
}

type NavigationListKey = Exclude<keyof ConversationCheckpointSemanticNavigation, 'authority'>

const NAVIGATION_LABELS: Array<[NavigationListKey, string]> = [
  ['current_objectives', '当前目标'],
  ['resolved_decisions', '已确认决定'],
  ['superseded_directions', '已被替代的方向'],
  ['unresolved_questions', '未解决问题'],
  ['next_context_needed', '后续需要读取'],
]

function tokenLabel(value?: number | null) {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${new Intl.NumberFormat('zh-CN').format(value)} tokens`
    : '未提供'
}

function modelLabel(state: ConversationContextState) {
  const binding = state.model_binding
  if (binding?.display_name) return binding.display_name
  if (binding?.provider && binding?.model) return `${binding.provider}:${binding.model}`
  if (binding?.model) return binding.model
  if (state.provider && state.model && !state.model.startsWith(`${state.provider}:`)) {
    return `${state.provider}:${state.model}`
  }
  return state.model || state.provider || '未提供'
}

function sourceRangeLabel(state: ConversationContextState) {
  const range = state.source_range
  const first = range?.first_sequence
  const last = range?.last_sequence
  const count = range?.message_count ?? state.source_message_count
  if (typeof first === 'number' && typeof last === 'number') {
    return `消息序号 ${first}–${last}${typeof count === 'number' ? `（${count} 条）` : ''}`
  }
  return typeof count === 'number' ? `${count} 条消息` : '未提供'
}

function sourceTimeLabel(state: ConversationContextState) {
  const started = formatApiDateTime(state.source_range?.started_at)
  const ended = formatApiDateTime(state.source_range?.ended_at)
  if (started && ended) return `${started} 至 ${ended}`
  return started || ended || '未提供'
}

function stateFromDetail(
  state: ConversationContextState,
  detail?: ConversationCheckpointDetail | null,
): ConversationContextState {
  return detail ? { ...state, ...detail } : state
}

interface ConversationCheckpointNoticeProps {
  state: ConversationContextState | null
  detail?: ConversationCheckpointDetail | null
  stateError?: string | null
  detailLoading?: boolean
  actionLoading?: 'rebuild' | 'cancel' | null
  modalOpen: boolean
  onOpen: () => void
  onClose: () => void
  onRebuild: () => void
  onCancel: () => void
  onNewConversation: () => void
  onJumpToMessage?: (messageId: string) => void
  canCancel?: boolean
}

function CheckpointDetailBody({
  state,
  detail,
  onJumpToMessage,
}: Pick<ConversationCheckpointNoticeProps, 'state' | 'detail' | 'onJumpToMessage'>) {
  if (!state) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无上下文整理信息" />
  const visible = stateFromDetail(state, detail)
  const navigation = detail?.semantic_navigation
  const quotes = detail?.author_quotes || []
  const ledger = detail?.execution_ledger || []
  const warnings = [...new Set([...(state.warnings || []), ...(detail?.warnings || [])])]
  return (
    <div className="conversation-checkpoint-detail">
      <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="状态">
          <Tag color={visible.status === 'ready' ? 'green' : visible.status === 'failed' ? 'red' : 'blue'}>
            {STATUS_LABELS[visible.status] || visible.status}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="触发原因">
          {TRIGGER_LABELS[String(visible.trigger || '')] || visible.trigger || '未提供'}
        </Descriptions.Item>
        <Descriptions.Item label="覆盖范围">{sourceRangeLabel(visible)}</Descriptions.Item>
        <Descriptions.Item label="覆盖时间">{sourceTimeLabel(visible)}</Descriptions.Item>
        <Descriptions.Item label="上下文 token">
          原始 {tokenLabel(visible.original_history_tokens)} → 活动 {tokenLabel(visible.active_history_tokens)}
          {typeof visible.checkpoint_tokens === 'number' ? `；checkpoint ${tokenLabel(visible.checkpoint_tokens)}` : ''}
        </Descriptions.Item>
        <Descriptions.Item label="保留原文">
          {typeof visible.recent_exact_turn_count === 'number'
            ? `最近 ${visible.recent_exact_turn_count} 个完整回合`
            : '未提供'}
        </Descriptions.Item>
        <Descriptions.Item label="模型">{modelLabel(visible)}</Descriptions.Item>
        <Descriptions.Item label="容量保证">
          {ASSURANCE_LABELS[String(visible.capacity_assurance || '')]
            || visible.capacity_assurance
            || '未提供'}
        </Descriptions.Item>
        <Descriptions.Item label="版本">
          policy {visible.policy_version ?? '—'} · schema {visible.schema_version ?? detail?.schema ?? '—'}
        </Descriptions.Item>
        {(visible.error_code || visible.error_detail) && (
          <Descriptions.Item label="失败信息">
            <Text type="danger">{visible.error_detail || visible.error_code}</Text>
            {visible.error_code && visible.error_detail && <Text type="secondary">（{visible.error_code}）</Text>}
          </Descriptions.Item>
        )}
      </Descriptions>

      {warnings.length > 0 && (
        <Alert
          className="conversation-checkpoint-detail-section"
          type="warning"
          showIcon
          message="整理警告"
          description={<ul>{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
        />
      )}

      <Divider orientation="left">作者原话</Divider>
      {quotes.length > 0 ? (
        <List
          size="small"
          dataSource={quotes}
          renderItem={(quote) => (
            <List.Item
              actions={onJumpToMessage ? [
                <Button key="jump" type="link" size="small" onClick={() => onJumpToMessage(quote.message_id)}>
                  跳到原消息
                </Button>,
              ] : undefined}
            >
              <div>
                <Paragraph className="conversation-checkpoint-quote">“{quote.exact_quote}”</Paragraph>
                <Space size={4} wrap>
                  {quote.purpose && <Tag>{quote.purpose}</Tag>}
                  {quote.superseded && <Tag color="orange">已被后续要求替代</Tag>}
                  <Text type="secondary">消息 {quote.message_id}</Text>
                </Space>
              </div>
            </List.Item>
          )}
        />
      ) : <Text type="secondary">此 checkpoint 没有需要逐字保留的作者原话。</Text>}

      <Divider orientation="left">真实执行回执</Divider>
      {ledger.length > 0 ? (
        <List
          size="small"
          dataSource={ledger}
          renderItem={(entry) => (
            <List.Item>
              <div className="conversation-checkpoint-ledger-entry">
                <Space size={6} wrap>
                  <Text code>{entry.tool || 'operation'}</Text>
                  <Tag color={entry.status === 'ok' || entry.status === 'completed' ? 'green' : 'red'}>
                    {entry.status || 'unknown'}
                  </Tag>
                  {entry.step_id && <Text type="secondary">步骤 {entry.step_id}</Text>}
                </Space>
                {entry.detail && <Paragraph>{entry.detail}</Paragraph>}
                {(entry.resource_refs || []).length > 0 && (
                  <Space size={4} wrap>
                    {(entry.resource_refs || []).map((resource) => (
                      <Tag key={`${resource.type || 'resource'}:${resource.id}`}>
                        {resource.type || 'resource'} · {resource.id}
                        {resource.revision !== null && resource.revision !== undefined ? ` · r${resource.revision}` : ''}
                      </Tag>
                    ))}
                  </Space>
                )}
              </div>
            </List.Item>
          )}
        />
      ) : <Text type="secondary">覆盖范围内没有需要保留的写入回执。</Text>}

      <Divider orientation="left">
        非权威语义导航 <InfoCircleOutlined aria-label="仅用于帮助模型定位历史，不替代项目真实数据" />
      </Divider>
      {navigation && NAVIGATION_LABELS.some(([key]) => (navigation[key] || []).length > 0) ? (
        <div className="conversation-checkpoint-navigation">
          {NAVIGATION_LABELS.map(([key, label]) => {
            const items = navigation[key] || []
            if (items.length === 0) return null
            return (
              <div key={key}>
                <Text strong>{label}</Text>
                <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
              </div>
            )
          })}
        </div>
      ) : <Text type="secondary">没有可显示的语义导航。</Text>}
    </div>
  )
}

export function ConversationCheckpointNotice({
  state,
  detail,
  stateError,
  detailLoading = false,
  actionLoading = null,
  modalOpen,
  onOpen,
  onClose,
  onRebuild,
  onCancel,
  onNewConversation,
  onJumpToMessage,
  canCancel = true,
}: ConversationCheckpointNoticeProps) {
  const status = state?.status || ''
  const checkpointId = checkpointIdForState(state, detail)
  const failed = status === 'failed'
  const cancelled = status === 'cancelled'
  const working = status === 'pending' || status === 'compressing'
  const ready = status === 'ready'
  const visibleState = Boolean(
    state
      && (status !== 'ready' || checkpointId),
  )
  const visible = visibleState || Boolean(stateError)
  if (!visible) return null

  const alertType = failed ? 'error' : cancelled || stateError ? 'warning' : 'info'
  const title = stateError
    ? '无法读取上下文整理状态'
    : ready
      ? '已整理较早上下文'
      : working
        ? '正在整理较早上下文'
        : failed
          ? '较早上下文整理失败'
          : cancelled
            ? '较早上下文整理已取消'
            : '上下文整理状态已更新'
  const description = stateError
    || (ready
      ? `保留最近 ${state?.recent_exact_turn_count ?? '—'} 轮原文；完整聊天记录未删除。`
      : working
        ? '当前业务任务尚未开始；整理完成并通过容量检查后会自动继续。'
        : failed
          ? `${state?.error_detail || '未能生成可用 checkpoint。'} 当前任务尚未执行，完整聊天仍然保留。`
          : cancelled
            ? '当前任务尚未执行，完整聊天仍然保留。'
            : '当前 checkpoint 不再适用于最新会话状态。')

  return (
    <>
      <Alert
        className="conversation-checkpoint-notice"
        data-testid="conversation-context-notice"
        type={alertType}
        showIcon
        message={title}
        description={description}
        action={(
          <Space size={4} wrap>
            {state && <Button size="small" icon={<HistoryOutlined />} onClick={onOpen}>查看</Button>}
            {(failed || cancelled) && state?.retryable !== false && (
              <Button
                size="small"
                type="primary"
                icon={<ReloadOutlined />}
                onClick={onRebuild}
              >发送新消息重试</Button>
            )}
            {canCancel && working && checkpointId && (
              <Button
                size="small"
                icon={<StopOutlined />}
                loading={actionLoading === 'cancel'}
                onClick={onCancel}
              >取消整理</Button>
            )}
          </Space>
        )}
      />
      <Modal
        title="上下文整理详情"
        width={760}
        open={modalOpen}
        onCancel={onClose}
        footer={(
          <Space wrap>
            <Button onClick={onClose}>关闭</Button>
            <Button onClick={onNewConversation}>新建对话</Button>
            {canCancel && working && checkpointId && (
              <Button loading={actionLoading === 'cancel'} onClick={onCancel}>取消整理</Button>
            )}
            {(failed || cancelled) && state?.retryable !== false && (
              <Button
                type="primary"
                icon={<ReloadOutlined />}
                onClick={onRebuild}
              >发送新消息重试</Button>
            )}
          </Space>
        )}
      >
        {detailLoading
          ? <Paragraph type="secondary">正在读取 checkpoint 详情…</Paragraph>
          : <CheckpointDetailBody state={state} detail={detail} onJumpToMessage={onJumpToMessage} />}
      </Modal>
    </>
  )
}
