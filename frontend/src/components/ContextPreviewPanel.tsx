import { useState } from 'react'
import { Tag, Typography, Space, Tooltip } from 'antd'

const { Text } = Typography

interface ContextSection {
  category: string
  title: string
  source_type: string
  selection_reason: string
  used_chars: number
  score: number
  chunk_count?: number
}

interface ContextSnapshot {
  rag_used?: boolean
  total_used_chars?: number
  sections?: ContextSection[]
  explanations?: string[]
  warnings?: string[]
  fts_available?: boolean
  resolved_character_count?: number
  resolved_aliases?: Record<string, string>
}

interface ContextPreviewPanelProps {
  snapshot?: ContextSnapshot
  ragSections?: ContextSection[]
  explanations?: string[]
  warnings?: string[]
  totalUsedChars?: number
  ragUsed?: boolean
}

const CATEGORY_COLORS: Record<string, string> = {
  outline: 'blue',
  summary: 'cyan',
  characters: 'green',
  worldbuilding: 'purple',
  pinned: 'gold',
}

const CATEGORY_LABELS: Record<string, string> = {
  outline: '大纲',
  summary: '摘要',
  characters: '角色',
  worldbuilding: '世界观',
  pinned: '固定',
}

export function ContextPreviewPanel({
  snapshot,
  ragSections,
  explanations,
  warnings,
  totalUsedChars,
  ragUsed: ragUsedProp,
}: ContextPreviewPanelProps) {
  const [expanded, setExpanded] = useState(false)

  const sections = snapshot?.sections || ragSections || []
  const totalChars = snapshot?.total_used_chars || totalUsedChars || 0
  const allWarnings = snapshot?.warnings || warnings || []
  const allExplanations = snapshot?.explanations || explanations || []
  const ragUsed =
    snapshot?.rag_used ??
    ragUsedProp ??
    sections.some((s) => (s.chunk_count ?? 0) > 0)

  if (sections.length === 0) return null

  return (
    <div
      style={{
        marginTop: 8,
        border: '1px solid #f0f0f0',
        borderRadius: 6,
        padding: '8px 12px',
        background: '#fafafa',
        fontSize: 12,
      }}
    >
      <div
        style={{
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          userSelect: 'none',
        }}
        onClick={() => setExpanded(!expanded)}
      >
        <Text strong style={{ fontSize: 12 }}>
          {expanded ? '▼' : '▶'} 上下文概览
        </Text>
        <Tag color={ragUsed ? 'blue' : 'default'} style={{ fontSize: 11 }}>
          {ragUsed ? 'RAG' : '传统'}
        </Tag>
        <Text type="secondary" style={{ fontSize: 11 }}>
          {sections.length} 分区 / {totalChars} 字符
        </Text>
        {allWarnings.length > 0 && (
          <Tag color="orange" style={{ fontSize: 11 }}>
            {allWarnings.length} 条警告
          </Tag>
        )}
      </div>

      {expanded && (
        <div style={{ marginTop: 8 }}>
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            {sections.map((s, i) => (
              <div
                key={i}
                style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}
              >
                <Tag
                  color={CATEGORY_COLORS[s.category] || 'default'}
                  style={{ fontSize: 11, margin: 0 }}
                >
                  {CATEGORY_LABELS[s.category] || s.category}
                </Tag>
                <Text style={{ fontSize: 11, flex: 1, minWidth: 120 }}>
                  {s.selection_reason}
                </Text>
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {s.used_chars} 字符
                </Text>
                {s.score > 0 && (
                  <Tooltip title={`相关性得分: ${s.score}`}>
                    <Text type="secondary" style={{ fontSize: 10 }}>
                      ({s.score.toFixed(0)})
                    </Text>
                  </Tooltip>
                )}
                {(s.chunk_count ?? 0) > 0 && (
                  <Tag style={{ fontSize: 10 }}>{s.chunk_count} chunks</Tag>
                )}
              </div>
            ))}
          </Space>

          {snapshot?.resolved_aliases && Object.keys(snapshot.resolved_aliases).length > 0 && (
            <div style={{ marginTop: 6 }}>
              <Text type="secondary" style={{ fontSize: 11 }}>
                别名解析：
                {Object.entries(snapshot.resolved_aliases).map(([alias, name]) => (
                  <Tag key={alias} style={{ fontSize: 10, marginLeft: 4 }}>
                    {alias} &rarr; {name}
                  </Tag>
                ))}
              </Text>
            </div>
          )}

          {allExplanations.length > 0 && (
            <div style={{ marginTop: 8 }}>
              {allExplanations.map((e, i) => (
                <Text
                  key={i}
                  type="secondary"
                  style={{ display: 'block', fontSize: 11 }}
                >
                  {e}
                </Text>
              ))}
            </div>
          )}

          {allWarnings.length > 0 && (
            <div style={{ marginTop: 6 }}>
              {allWarnings.map((w, i) => (
                <Tag
                  key={i}
                  color="orange"
                  style={{ fontSize: 11, marginBottom: 4 }}
                >
                  {w}
                </Tag>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
