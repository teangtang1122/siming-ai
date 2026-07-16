/* Message list rendering for the assistant chat. */
import { Empty, Space, Tag, Tooltip, Typography } from 'antd'
import { DownOutlined } from '@ant-design/icons'
import { ChapterVersionPanel } from '../ChapterVersionPanel'
import { NarrativeLedgerPanel } from '../NarrativeLedgerPanel'
import { ContextPreviewPanel } from '../ContextPreviewPanel'
import { findStorageHealth, StorageRepairActions } from '../StorageRepairActions'
import { PersistentOutcome } from '../interaction'
import type { OperationOutcome } from '../interaction'
import type { WorkspaceAssistantMessage, SkillMatch, WorkspaceToolLog } from './types'
import { SCOPE_LABEL } from './constants'

const { Paragraph, Text } = Typography

function chapterVersionActions(item: WorkspaceAssistantMessage): WorkspaceToolLog[] {
  return [
    ...(item.data?.applied_actions || []),
    ...(item.data?.tool_logs || []),
  ].filter((action) => action.tool === 'list_chapter_versions' && action.data)
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
  matchedSkills: SkillMatch[]
  showScrollBottom: boolean
  onScrollToBottom: () => void
  messagesRef: React.RefObject<HTMLDivElement>
  onScroll: () => void
  projectId: string
  onStorageRepaired?: () => void
}

export function MessageList({
  messages,
  generating,
  matchedSkills,
  showScrollBottom,
  onScrollToBottom,
  messagesRef,
  onScroll,
  projectId,
  onStorageRepaired,
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
                {item.created_at && (
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {new Date(item.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                  </Text>
                )}
              </div>
              <Paragraph style={{ marginTop: 6, marginBottom: 6, whiteSpace: 'pre-wrap' }}>
                {item.content}
              </Paragraph>
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
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="直接提出需求，AI会读取项目资料并决定是否调用工具。" />
        )}

        {generating && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 0' }}>
            <div className="typing-indicator">
              <span /><span /><span />
            </div>
            <Text type="secondary" style={{ fontSize: 12 }}>AI 正在分析</Text>
          </div>
        )}

        {/* Matched skills indicator */}
        {matchedSkills.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>已激活技能：</Text>
            <Space size={[4, 4]} wrap>
              {matchedSkills.map((s) => (
                <Tooltip
                  key={s.name}
                  title={s.truncated ? `${s.description || ''}（提示词已截断）` : s.description}
                >
                  <Tag color={s.injected === false ? 'default' : 'blue'} style={{ fontSize: 11 }}>
                    {s.name}{s.truncated ? ' ⚠' : ''}
                  </Tag>
                </Tooltip>
              ))}
            </Space>
          </div>
        )}
      </div>
    </>
  )
}
