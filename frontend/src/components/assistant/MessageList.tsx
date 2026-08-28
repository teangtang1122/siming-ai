/* Message list rendering for the assistant chat. */
import { useState } from 'react'
import { Button, Dropdown, Empty, Space, Tag, Typography, message } from 'antd'
import { DatabaseOutlined, DownOutlined, SaveOutlined } from '@ant-design/icons'
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

function ChapterDraftSaveActions({
  action,
  activeDraftId,
  activeDraftStatus,
  onSave,
}: {
  action: WorkspaceToolLog
  activeDraftId: string | null
  activeDraftStatus: string | null
  onSave: (action: WorkspaceToolLog, mode: 'save_only' | 'save_and_catalog') => Promise<void>
}) {
  const [savingMode, setSavingMode] = useState<'save_only' | 'save_and_catalog' | null>(null)
  const draftId = String(action.data?.draft_id || '')
  const saved = activeDraftId === draftId && activeDraftStatus === 'saved'
  const superseded = activeDraftId === draftId && activeDraftStatus === 'superseded'
  const replaced = Boolean(activeDraftId && activeDraftId !== draftId)
  const save = async (mode: 'save_only' | 'save_and_catalog') => {
    if (savingMode) return
    setSavingMode(mode)
    try {
      await onSave(action, mode)
    } catch (error: any) {
      message.error(
        error?.response?.data?.detail
        || error?.message
        || '保存章节草稿失败，请重试',
      )
    } finally {
      setSavingMode(null)
    }
  }
  if (saved) return <Tag color="green">草稿已保存</Tag>
  if (superseded) return <Tag>迟到草稿已释放</Tag>
  if (replaced) return <Tag>已被当前草稿替代</Tag>
  return (
    <Dropdown.Button
      type="primary"
      size="small"
      icon={<SaveOutlined />}
      loading={savingMode !== null}
      disabled={savingMode !== null}
      onClick={() => void save('save_and_catalog')}
      menu={{
        items: [{ key: 'save_only', label: '仅保存', disabled: savingMode !== null }],
        onClick: ({ key }) => {
          if (key === 'save_only') void save('save_only')
        },
      }}
    >
      保存并建档
    </Dropdown.Button>
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
  activeDraftId?: string | null
  activeDraftStatus?: string | null
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
  activeDraftId = null,
  activeDraftStatus = null,
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
              {item.role === 'assistant' && onSaveChapterDraft && chapterDraftActions(item).map((action, actionIndex) => (
                <div className="workspace-assistant-message-action" key={`chapter-draft-${actionIndex}`}>
                  <ChapterDraftSaveActions
                    action={action}
                    activeDraftId={activeDraftId}
                    activeDraftStatus={activeDraftStatus}
                    onSave={onSaveChapterDraft}
                  />
                </div>
              ))}
              {item.role === 'assistant' && item.data?.outcome && PERSISTENT_OUTCOMES.has(item.data.outcome) && (
                <PersistentOutcome
                  outcome={item.data.outcome as OperationOutcome}
                  result={assistantOutcomeResult(item)}
                />
              )}

              {/* Context preview panels for chapter_writer / preview_writing_context */}
              {item.data?.applied_actions?.map((action, i) => {
                if (action.tool === 'chapter_writer' && action.data?.context_snapshot) {
                  return <ContextPreviewPanel key={`ctx-${i}`} snapshot={action.data.context_snapshot as any} />
                }
                if (action.tool === 'preview_writing_context' && action.data?.rag_sections) {
                  return (
                    <ContextPreviewPanel
                      key={`ctx-${i}`}
                      ragSections={action.data.rag_sections as any}
                      explanations={action.data.explanations as any}
                      warnings={action.data.warnings as any}
                      totalUsedChars={action.data.total_used_chars as number}
                      ragUsed={action.data.rag_used as boolean}
                    />
                  )
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
