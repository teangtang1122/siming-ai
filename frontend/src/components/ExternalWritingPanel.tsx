import { useState } from 'react'
import {
  Button,
  Card,
  Collapse,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  CopyOutlined,
  EditOutlined,
  SaveOutlined,
} from '@ant-design/icons'

const { Text, Paragraph } = Typography

interface ExternalWritingPanelProps {
  projectId: string
}

interface ContextSection {
  title: string
  content: string
}

function ExternalWritingPanel(_props: ExternalWritingPanelProps) {
  const [context] = useState<ContextSection[]>([])
  const [draft, setDraft] = useState('')
  const [loading] = useState(false)

  const handleCopyContext = () => {
    const text = context.map(s => `## ${s.title}\n${s.content}`).join('\n\n')
    navigator.clipboard.writeText(text)
    message.success('上下文已复制到剪贴板')
  }

  const handleCopyDraft = () => {
    if (draft) {
      navigator.clipboard.writeText(draft)
      message.success('草稿已复制到剪贴板')
    }
  }

  const handlePasteDraft = async () => {
    try {
      const text = await navigator.clipboard.readText()
      if (text) {
        setDraft(text)
        message.success('草稿已粘贴')
      }
    } catch {
      message.error('无法读取剪贴板')
    }
  }

  return (
    <Card
      size="small"
      title="外部写作工作流"
      style={{ marginBottom: 16 }}
    >
      <Paragraph type="secondary" style={{ marginBottom: 16 }}>
        使用此面板配合 Claude Code / Codex 进行外部写作。复制上下文给外部模型，粘贴生成的草稿。
      </Paragraph>

      {/* Context section */}
      <Collapse size="small" style={{ marginBottom: 16 }}>
        <Collapse.Panel
          key="context"
          header={
            <Space>
              <Text strong>写作上下文</Text>
              <Tag>{context.length} 个部分</Tag>
            </Space>
          }
          extra={
            <Button
              size="small"
              icon={<CopyOutlined />}
              onClick={(e) => {
                e.stopPropagation()
                handleCopyContext()
              }}
              disabled={context.length === 0}
            >
              复制
            </Button>
          }
        >
          {context.length === 0 ? (
            <Text type="secondary">暂无上下文。请先通过 MCP 工具获取写作上下文。</Text>
          ) : (
            context.map((section, i) => (
              <div key={i} style={{ marginBottom: 8 }}>
                <Text strong>{section.title}</Text>
                <Paragraph
                  style={{
                    maxHeight: 200,
                    overflow: 'auto',
                    background: '#f5f5f5',
                    padding: 8,
                    borderRadius: 4,
                    fontSize: 13,
                    whiteSpace: 'pre-wrap',
                    marginTop: 4,
                  }}
                >
                  {section.content}
                </Paragraph>
              </div>
            ))
          )}
        </Collapse.Panel>
      </Collapse>

      {/* Draft section */}
      <Card size="small" title="草稿" style={{ marginBottom: 16 }}>
        <Space style={{ marginBottom: 8 }}>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={handlePasteDraft}
          >
            粘贴草稿
          </Button>
          <Button
            size="small"
            icon={<CopyOutlined />}
            onClick={handleCopyDraft}
            disabled={!draft}
          >
            复制草稿
          </Button>
        </Space>
        {draft ? (
          <Paragraph
            style={{
              maxHeight: 300,
              overflow: 'auto',
              background: '#f5f5f5',
              padding: 8,
              borderRadius: 4,
              fontSize: 13,
              whiteSpace: 'pre-wrap',
            }}
          >
            {draft}
          </Paragraph>
        ) : (
          <Text type="secondary">暂无草稿。粘贴外部模型生成的文本。</Text>
        )}
      </Card>

      {/* Actions */}
      <Space>
        <Button
          type="primary"
          icon={<SaveOutlined />}
          disabled={!draft}
          loading={loading}
        >
          保存为草稿
        </Button>
      </Space>
    </Card>
  )
}

export default ExternalWritingPanel
