/* Message list rendering for the assistant chat. */
import { useState } from 'react'
import { Button, Dropdown, Empty, Popconfirm, Space, Tag, Typography, message } from 'antd'
import {
  BranchesOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  DiffOutlined,
  DownOutlined,
  ReloadOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { ChapterVersionPanel } from '../ChapterVersionPanel'
import { NarrativeLedgerPanel } from '../NarrativeLedgerPanel'
import { ContextPreviewPanel } from '../ContextPreviewPanel'
import { findStorageHealth, StorageRepairActions } from '../StorageRepairActions'
import { PersistentOutcome } from '../interaction'
import type { OperationOutcome } from '../interaction'
import { AssistantMessageTime } from './MessageTime'
import { ReasoningDisclosure } from './ReasoningDisclosure'
import type {
  WorkspaceAssistantMessage,
  WorkspaceMessageNavigationAction,
  WorkspaceToolLog,
} from './types'
import { SCOPE_LABEL } from './constants'
import './MessageTime.css'

const { Paragraph, Text } = Typography

function MessageNavigationButton({
  action,
  urgent,
}: {
  action: WorkspaceMessageNavigationAction
  urgent: boolean
}) {
  const navigate = useNavigate()
  return (
    <Button
      type={urgent ? 'primary' : 'default'}
      size="small"
      icon={<DatabaseOutlined />}
      onClick={() => navigate(action.to)}
    >
      {action.label}
    </Button>
  )
}

function chapterVersionActions(item: WorkspaceAssistantMessage): WorkspaceToolLog[] {
  return [
    ...(item.data?.applied_actions || []),
    ...(item.data?.tool_logs || []),
  ].filter((action) => action.tool === 'list_chapter_versions' && action.data)
}

function chapterDraftActions(item: WorkspaceAssistantMessage): WorkspaceToolLog[] {
  return (item.data?.applied_actions || []).filter((action) => (
    ['chapter_writer', 'save_external_chapter_draft'].includes(String(action.tool || ''))
    && action.status === 'ok'
    && action.data?.draft_id
    && action.data?.content
  ))
}

function outlineDraftActions(item: WorkspaceAssistantMessage): WorkspaceToolLog[] {
  return (item.data?.applied_actions || []).filter((action) => (
    ['outline_writer', 'save_external_outline_draft'].includes(String(action.tool || ''))
    && action.status === 'ok'
    && action.data?.draft_id
    && Array.isArray(action.data?.nodes)
  ))
}

type OutlineDraftAction = 'open' | 'confirm' | 'confirm_and_write' | 'regenerate' | 'discard'

function OutlineDraftReviewActions({
  action,
  activeDraftId,
  activeDraftStatus,
  onAction,
}: {
  action: WorkspaceToolLog
  activeDraftId: string | null
  activeDraftStatus: string | null
  onAction: (action: WorkspaceToolLog, mode: OutlineDraftAction) => Promise<void>
}) {
  const [working, setWorking] = useState<OutlineDraftAction | null>(null)
  const draftId = String(action.data?.draft_id || '')
  const status = activeDraftId === draftId
    ? activeDraftStatus
    : String(action.data?.draft_status || 'pending')
  const replaced = Boolean(activeDraftId && activeDraftId !== draftId)
  const run = async (mode: OutlineDraftAction) => {
    if (working) return
    setWorking(mode)
    try {
      await onAction(action, mode)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || error?.message || '处理大纲草稿失败')
    } finally {
      setWorking(null)
    }
  }
  if (status === 'confirmed') return <Tag color="green">大纲已确认</Tag>
  if (status === 'discarded' || replaced) return <Tag>草稿已失效</Tag>
  return (
    <Space size={4} wrap>
      <Button size="small" icon={<BranchesOutlined />} onClick={() => void run('open')}>查看并编辑</Button>
      <Button size="small" type="primary" icon={<SaveOutlined />} loading={working === 'confirm'} onClick={() => void run('confirm')}>确认大纲</Button>
      <Button size="small" type="primary" loading={working === 'confirm_and_write'} onClick={() => void run('confirm_and_write')}>确认并写章</Button>
      <Button size="small" icon={<ReloadOutlined />} loading={working === 'regenerate'} onClick={() => void run('regenerate')}>重新规划</Button>
      <Button size="small" danger icon={<DeleteOutlined />} loading={working === 'discard'} onClick={() => void run('discard')}>丢弃</Button>
    </Space>
  )
}

function ChapterDraftSaveActions({
  action,
  activeDraftId,
  activeDraftStatus,
  onSave,
  onDiscard,
}: {
  action: WorkspaceToolLog
  activeDraftId: string | null
  activeDraftStatus: string | null
  onSave: (action: WorkspaceToolLog, mode: 'save_only' | 'save_and_catalog') => Promise<void>
  onDiscard: (action: WorkspaceToolLog) => Promise<void>
}) {
  const [working, setWorking] = useState<'save_only' | 'save_and_catalog' | 'discard' | null>(null)
  const [localStatus, setLocalStatus] = useState<string | null>(null)
  const draftId = String(action.data?.draft_id || '')
  const status = localStatus || (activeDraftId === draftId
    ? activeDraftStatus
    : String(action.data?.draft_status || 'pending'))
  const replaced = Boolean(activeDraftId && activeDraftId !== draftId)
  const revision = action.data?.draft_kind === 'revision'
  const save = async (mode: 'save_only' | 'save_and_catalog') => {
    if (working) return
    setWorking(mode)
    try {
      await onSave(action, mode)
    } catch (error: any) {
      message.error(
        error?.response?.data?.detail
        || error?.message
        || '保存章节草稿失败，请重试',
      )
    } finally {
      setWorking(null)
    }
  }
  const discard = async () => {
    if (working) return
    setWorking('discard')
    try {
      await onDiscard(action)
      setLocalStatus('discarded')
    } catch (error: any) {
      message.error(
        error?.response?.data?.detail
        || error?.message
        || '丢弃章节草稿失败，请重试',
      )
    } finally {
      setWorking(null)
    }
  }
  if (status === 'saved') return <Tag color="green">草稿已保存</Tag>
  if (status === 'discarded') return <Tag>草稿已丢弃</Tag>
  if (status === 'superseded') return <Tag>迟到草稿已释放</Tag>
  if (replaced) return <Tag>已被当前草稿替代</Tag>
  const discardButton = (
    <Popconfirm
      title="丢弃这份章节草稿？"
      description="草稿会被释放，已有正式正文不会改变。"
      okText="丢弃"
      cancelText="取消"
      okButtonProps={{ danger: true }}
      onConfirm={() => discard()}
    >
      <Button
        size="small"
        danger
        icon={<DeleteOutlined />}
        loading={working === 'discard'}
        disabled={working !== null}
      >
        丢弃
      </Button>
    </Popconfirm>
  )
  if (revision) {
    return (
      <Space size={4} wrap>
        <Button
          type="primary"
          size="small"
          icon={<DiffOutlined />}
          loading={working === 'save_only'}
          disabled={working !== null}
          onClick={() => void save('save_only')}
        >
          在正文页审阅修订
        </Button>
        {discardButton}
      </Space>
    )
  }
  return (
    <Space size={4} wrap>
      <Dropdown.Button
        type="primary"
        size="small"
        icon={<SaveOutlined />}
        loading={working === 'save_and_catalog' || working === 'save_only'}
        disabled={working !== null}
        onClick={() => void save('save_and_catalog')}
        menu={{
          items: [{ key: 'save_only', label: '仅保存', disabled: working !== null }],
          onClick: ({ key }) => {
            if (key === 'save_only') void save('save_only')
          },
        }}
      >
        保存并建档
      </Dropdown.Button>
      {discardButton}
    </Space>
  )
}

const PERSISTENT_OUTCOMES = new Set([
  'partial_success',
  'empty_response',
  'skipped_preflight',
  'waiting_user',
  'blocked',
  'failed',
])

function assistantOutcomeResult(item: WorkspaceAssistantMessage) {
  const completed = (item.data?.applied_actions || [])
    .filter((action) => action.status !== 'error')
    .map((action) => action.detail || action.tool || '工具操作')
  const incomplete = (item.data?.tool_logs || [])
    .filter((action) => action.status === 'error')
    .map((action) => action.detail || action.tool || '工具操作')
  return { completed, incomplete }
}

interface MessageListProps {
  messages: WorkspaceAssistantMessage[]
  generating: boolean
  showScrollBottom: boolean
  onScrollToBottom: () => void
  messagesRef: React.RefObject<HTMLDivElement>
  onScroll: () => void
  projectId: string
  onStorageRepaired?: () => void
  emptyDescription?: string
  onSaveChapterDraft?: (
    action: WorkspaceToolLog,
    mode: 'save_only' | 'save_and_catalog',
  ) => Promise<void>
  onDiscardChapterDraft?: (action: WorkspaceToolLog) => Promise<void>
  activeDraftId?: string | null
  activeDraftStatus?: string | null
  onOutlineDraftAction?: (
    action: WorkspaceToolLog,
    mode: OutlineDraftAction,
  ) => Promise<void>
  activeOutlineDraftId?: string | null
  activeOutlineDraftStatus?: string | null
}

export function MessageList({
  messages,
  generating,
  showScrollBottom,
  onScrollToBottom,
  messagesRef,
  onScroll,
  projectId,
  onStorageRepaired,
  emptyDescription = '直接提出需求，AI会读取项目资料并决定是否调用工具。',
  onSaveChapterDraft,
  onDiscardChapterDraft,
  activeDraftId = null,
  activeDraftStatus = null,
  onOutlineDraftAction,
  activeOutlineDraftId = null,
  activeOutlineDraftStatus = null,
}: MessageListProps) {
  return (
    <>
      <div
        className="workspace-assistant-messages"
        ref={messagesRef}
        onScroll={onScroll}
      >
        {showScrollBottom && (
          <button
            type="button"
            className="workspace-assistant-scroll-bottom"
            onClick={onScrollToBottom}
            title="滚动到底部"
          >
            <DownOutlined />
          </button>
        )}

        {messages.length > 0 ? (
          messages.map((item, index) => (
            <div
              key={`${item.role}-${item.id || index}`}
              data-message-id={item.id || undefined}
              className={`workspace-assistant-message workspace-assistant-${item.role}`}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <Tag
                  color={
                    item.role === 'user'
                      ? 'default'
                      : item.status === 'error'
                        ? 'red'
                        : item.status === 'aborted'
                          ? 'orange'
                          : 'blue'
                  }
                >
                  {item.role === 'user' ? '你' : SCOPE_LABEL}
                </Tag>
                <AssistantMessageTime value={item.created_at} />
              </div>
              {item.role === 'assistant' && (
                <ReasoningDisclosure
                  content={item.reasoning_content}
                  streaming={generating && item.status === 'running'}
                />
              )}
              <Paragraph style={{ marginTop: 6, marginBottom: 6, whiteSpace: 'pre-wrap' }}>
                {item.content}
              </Paragraph>
              {item.role === 'assistant' && item.navigation_action && (
                <div className="workspace-assistant-message-action">
                  <MessageNavigationButton
                    action={item.navigation_action}
                    urgent={item.status === 'blocked' || item.status === 'error'}
                  />
                </div>
              )}
              {item.role === 'assistant' && onSaveChapterDraft && onDiscardChapterDraft && chapterDraftActions(item).map((action, actionIndex) => (
                <div className="workspace-assistant-message-action" key={`chapter-draft-${actionIndex}`}>
                  <ChapterDraftSaveActions
                    action={action}
                    activeDraftId={activeDraftId}
                    activeDraftStatus={activeDraftStatus}
                    onSave={onSaveChapterDraft}
                    onDiscard={onDiscardChapterDraft}
                  />
                </div>
              ))}
              {item.role === 'assistant' && onOutlineDraftAction && outlineDraftActions(item).map((action, actionIndex) => (
                <div className="workspace-assistant-message-action" key={`outline-draft-${actionIndex}`}>
                  <OutlineDraftReviewActions
                    action={action}
                    activeDraftId={activeOutlineDraftId}
                    activeDraftStatus={activeOutlineDraftStatus}
                    onAction={onOutlineDraftAction}
                  />
                </div>
              ))}
              {item.role === 'assistant' && item.data?.outcome && PERSISTENT_OUTCOMES.has(item.data.outcome) && (
                <PersistentOutcome
                  outcome={item.data.outcome as OperationOutcome}
                  result={assistantOutcomeResult(item)}
                />
              )}

              {/* Final model-selected context snapshot for chapter drafts */}
              {item.data?.applied_actions?.map((action, i) => {
                if (action.tool === 'chapter_writer' && action.data?.context_snapshot) {
                  return <ContextPreviewPanel key={`ctx-${i}`} snapshot={action.data.context_snapshot as any} />
                }
                return null
              })}

              {/* Applied action tags */}
              {item.data?.applied_actions && item.data.applied_actions.length > 0 && (
                <Space wrap size={4}>
                  {item.data.applied_actions.map((action, actionIndex) => (
                    <Tag key={`${action.tool}-${actionIndex}`} color={action.status === 'ok' ? 'green' : 'orange'}>
                      {action.detail || action.tool}
                    </Tag>
                  ))}
                </Space>
              )}
              {item.role === 'assistant' && (
                <StorageRepairActions
                  projectId={projectId}
                  health={findStorageHealth(item.data)}
                  onRepaired={onStorageRepaired}
                />
              )}
              {item.role === 'assistant' && chapterVersionActions(item).map((action, actionIndex) => (
                <ChapterVersionPanel
                  key={`chapter-version-${actionIndex}`}
                  projectId={projectId}
                  data={action.data}
                  onRestored={onStorageRepaired}
                />
              ))}
              {item.role === 'assistant' && item.data && <NarrativeLedgerPanel data={item.data} />}
            </div>
          ))
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyDescription} />
        )}

        {generating && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 0' }}>
            <div className="typing-indicator">
              <span /><span /><span />
            </div>
            <Text type="secondary" style={{ fontSize: 12 }}>AI 正在分析</Text>
          </div>
        )}

      </div>
    </>
  )
}
