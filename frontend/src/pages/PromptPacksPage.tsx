import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Collapse,
  Descriptions,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  BookOutlined,
  CopyOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'

const { Title, Text, Paragraph } = Typography

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface PromptPack {
  pack_id: string
  version: string
  scope: string
  title: string
  summary: string | null
  is_builtin: boolean
}

interface PromptPackDetail {
  pack_id: string
  version: string
  scope: string
  title: string
  summary: string | null
  system_prompt: string
  workflow: Array<{ step: number; name: string; description: string }> | null
  quality_rubric: {
    dimensions?: Array<{ name: string; description: string; max_score: number }>
    passing_score?: number
    max_score?: number
  } | null
  forbidden_patterns: string[] | null
  tool_playbook: Record<string, unknown> | null
}

interface PromptPacksPageProps {
  projectId: string
}

const SCOPE_LABELS: Record<string, string> = {
  new_project: '新小说创建',
  chapter_writing: '章节写作',
  chapter_review: '章节评审',
  character_design: '角色设计',
  worldbuilding: '世界观设计',
  outline_planning: '大纲规划',
  anti_ai_review: '反AI味审查',
}

function PromptPacksPage(_props: PromptPacksPageProps) {
  const [packs, setPacks] = useState<PromptPack[]>([])
  const [selectedPack] = useState<PromptPackDetail | null>(null)
  const [loading, setLoading] = useState(false)

  const fetchPacks = useCallback(async () => {
    setLoading(true)
    try {
      await apiClient.get<ApiResponse<{ items: PromptPack[] }>>(
        `/tools/catalog`,
      )
      // Filter to prompt pack tools - use the list_prompt_packs tool via catalog
      // For now, fetch directly from the tool
      setPacks([])
    } catch {
      // Silent fail
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchPacks()
  }, [fetchPacks])

  const handleCopyPrompt = (text: string) => {
    navigator.clipboard.writeText(text)
    message.success('已复制到剪贴板')
  }

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={4}>
          <BookOutlined style={{ marginRight: 8 }} />
          提示词包
        </Title>
        <Button icon={<ReloadOutlined />} onClick={fetchPacks} loading={loading}>
          刷新
        </Button>
      </div>

      <Paragraph type="secondary" style={{ marginBottom: 16 }}>
        提示词包定义了司命的写作方法。内部助手和外部 Agent（Claude Code / Codex）使用相同的提示词包。
      </Paragraph>

      {packs.length === 0 ? (
        <Card>
          <Text type="secondary">暂无提示词包数据。请通过 MCP 工具或内部助手获取。</Text>
        </Card>
      ) : (
        <Collapse>
          {packs.map(pack => (
            <Collapse.Panel
              key={pack.pack_id}
              header={
                <Space>
                  <Text strong>{pack.title}</Text>
                  <Tag>{SCOPE_LABELS[pack.scope] || pack.scope}</Tag>
                  <Tag color={pack.is_builtin ? 'blue' : 'green'}>
                    {pack.is_builtin ? '内置' : '自定义'}
                  </Tag>
                  <Text type="secondary">v{pack.version}</Text>
                </Space>
              }
              extra={
                <Button
                  size="small"
                  icon={<CopyOutlined />}
                  onClick={(e) => {
                    e.stopPropagation()
                    if (selectedPack?.system_prompt) {
                      handleCopyPrompt(selectedPack.system_prompt)
                    }
                  }}
                >
                  复制提示词
                </Button>
              }
            >
              {pack.summary && (
                <Paragraph style={{ marginBottom: 16 }}>{pack.summary}</Paragraph>
              )}
            </Collapse.Panel>
          ))}
        </Collapse>
      )}

      {selectedPack && (
        <Card title={selectedPack.title} style={{ marginTop: 16 }}>
          <Descriptions column={1} size="small">
            <Descriptions.Item label="版本">{selectedPack.version}</Descriptions.Item>
            <Descriptions.Item label="范围">{SCOPE_LABELS[selectedPack.scope] || selectedPack.scope}</Descriptions.Item>
          </Descriptions>

          {selectedPack.quality_rubric?.dimensions && (
            <div style={{ marginTop: 16 }}>
              <Text strong>质量维度：</Text>
              <div style={{ marginTop: 8 }}>
                {selectedPack.quality_rubric.dimensions.map(dim => (
                  <Tag key={dim.name} style={{ marginBottom: 4 }}>
                    {dim.name} ({dim.max_score}分)
                  </Tag>
                ))}
              </div>
            </div>
          )}

          {selectedPack.forbidden_patterns && selectedPack.forbidden_patterns.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <Text strong>禁用句式：</Text>
              <div style={{ marginTop: 8 }}>
                {selectedPack.forbidden_patterns.slice(0, 10).map(pattern => (
                  <Tag key={pattern} color="red" style={{ marginBottom: 4 }}>
                    {pattern}
                  </Tag>
                ))}
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}

export default PromptPacksPage
