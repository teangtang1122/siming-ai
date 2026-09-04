import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  Input,
  Popconfirm,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  DeleteOutlined,
  ReloadOutlined,
  SaveOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import {
  useAiPanelContext,
  type GeneratedOutlineDraft,
  type GeneratedOutlineDraftNode,
} from '../contexts/AiPanelContext'

const { Paragraph, Text, Title } = Typography

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface OutlineDraftReviewPanelProps {
  projectId: string
  draft: GeneratedOutlineDraft
  onFormalOutlineChanged: () => void | Promise<void>
}

const NODE_TYPES = [
  { value: 'volume', label: '卷' },
  { value: 'chapter', label: '章' },
  { value: 'section', label: '节' },
]

export function OutlineDraftReviewPanel({
  projectId,
  draft,
  onFormalOutlineChanged,
}: OutlineDraftReviewPanelProps) {
  const navigate = useNavigate()
  const {
    updateGeneratedOutlineDraft,
    requestAuthorAgentTurn,
    triggerRefresh,
  } = useAiPanelContext()
  const [nodes, setNodes] = useState<GeneratedOutlineDraftNode[]>(draft.nodes)
  const [designNotes, setDesignNotes] = useState(draft.designNotes)
  const [working, setWorking] = useState<string | null>(null)

  useEffect(() => {
    setNodes(draft.nodes)
    setDesignNotes(draft.designNotes)
  }, [draft.designNotes, draft.draftId, draft.nodes])

  const updateNode = (index: number, partial: Partial<GeneratedOutlineDraftNode>) => {
    setNodes((current) => current.map((node, nodeIndex) => (
      nodeIndex === index ? { ...node, ...partial } : node
    )))
  }

  const persist = async () => {
    const response = await apiClient.put<ApiResponse<Record<string, any>>>(
      `/projects/${projectId}/outline-drafts/${draft.draftId}`,
      {
        nodes,
        design_notes: designNotes,
      },
    )
    updateGeneratedOutlineDraft({ nodes, designNotes })
    return response.data.data
  }

  const saveEdits = async () => {
    setWorking('save')
    try {
      await persist()
      message.success('大纲草稿已保存，仍未写入正式大纲')
    } catch (error: any) {
      message.error(error.message || '保存大纲草稿失败')
    } finally {
      setWorking(null)
    }
  }

  const confirm = async (writeAfterConfirm: boolean) => {
    setWorking(writeAfterConfirm ? 'confirm_and_write' : 'confirm')
    try {
      await persist()
      const response = await apiClient.post<ApiResponse<{
        saved_outline_node_ids?: string[]
        next_author_request?: { message?: string }
      }>>(
        `/projects/${projectId}/outline-drafts/${draft.draftId}/confirm`,
        { write_after_confirm: writeAfterConfirm },
      )
      updateGeneratedOutlineDraft({
        status: 'confirmed',
        savedOutlineNodeIds: response.data.data.saved_outline_node_ids || [],
      })
      triggerRefresh()
      await Promise.resolve(onFormalOutlineChanged())
      if (writeAfterConfirm) {
        const nextMessage = String(response.data.data.next_author_request?.message || '')
        if (nextMessage) {
          requestAuthorAgentTurn({ projectId, message: nextMessage })
          navigate(`/project/${encodeURIComponent(projectId)}?view=outline&assistant=open`)
          message.success('大纲已确认；新的写章任务将以作者消息单独发起')
        } else {
          message.warning('大纲已确认，但没有可写的章级节点')
        }
      } else {
        message.success('大纲已确认并写入正式大纲')
      }
    } catch (error: any) {
      message.error(error.message || '确认大纲失败')
    } finally {
      setWorking(null)
    }
  }

  const regenerate = async () => {
    setWorking('regenerate')
    try {
      const response = await apiClient.post<ApiResponse<{
        next_author_request?: { message?: string }
      }>>(`/projects/${projectId}/outline-drafts/${draft.draftId}/regenerate`)
      updateGeneratedOutlineDraft({ status: 'superseded' })
      triggerRefresh()
      const nextMessage = String(response.data.data.next_author_request?.message || '')
      if (nextMessage) {
        requestAuthorAgentTurn({ projectId, message: nextMessage })
        navigate(`/project/${encodeURIComponent(projectId)}?view=outline&assistant=open`)
      }
    } catch (error: any) {
      message.error(error.message || '重新规划失败')
    } finally {
      setWorking(null)
    }
  }

  const discard = async () => {
    setWorking('discard')
    try {
      await apiClient.delete(`/projects/${projectId}/outline-drafts/${draft.draftId}`)
      updateGeneratedOutlineDraft({ status: 'discarded' })
      triggerRefresh()
      message.success('大纲草稿已丢弃')
    } catch (error: any) {
      message.error(error.message || '丢弃大纲草稿失败')
    } finally {
      setWorking(null)
    }
  }

  if (draft.status !== 'pending') return null

  return (
    <Card
      size="small"
      className="outline-draft-review"
      title={(
        <Space>
          <Title level={5} style={{ margin: 0 }}>AI 大纲草稿</Title>
          <Tag color="gold">未保存</Tag>
        </Space>
      )}
      extra={<Text type="secondary">{nodes.length} 个节点</Text>}
    >
      <Alert
        type="info"
        showIcon
        message="这些节点只存在于草稿区"
        description="你可以先修改。点击确认前，它们不会进入正式大纲，也不会触发正文写作。"
        style={{ marginBottom: 12 }}
      />
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {nodes.map((node, index) => (
          <Card key={`${draft.draftId}-node-${index}`} size="small" type="inner" title={`节点 ${index + 1}`}>
            <div className="outline-grid">
              <Select
                aria-label={`草稿节点 ${index + 1} 类型`}
                value={node.node_type}
                options={NODE_TYPES}
                onChange={(value) => updateNode(index, { node_type: value })}
              />
              <Input
                aria-label={`草稿节点 ${index + 1} 标题`}
                value={node.title}
                maxLength={200}
                onChange={(event) => updateNode(index, { title: event.target.value })}
              />
            </div>
            {node.node_type === 'section' && (
              <Input
                style={{ marginTop: 8 }}
                aria-label={`草稿节点 ${index + 1} 父标题`}
                placeholder="所属章标题"
                value={node.parent_title || ''}
                onChange={(event) => updateNode(index, { parent_title: event.target.value || null })}
              />
            )}
            <Input.TextArea
              style={{ marginTop: 8 }}
              aria-label={`草稿节点 ${index + 1} 摘要`}
              value={node.summary || ''}
              autoSize={{ minRows: 3, maxRows: 8 }}
              onChange={(event) => updateNode(index, { summary: event.target.value })}
            />
            <Select
              mode="tags"
              style={{ width: '100%', marginTop: 8 }}
              aria-label={`草稿节点 ${index + 1} 角色`}
              placeholder="涉及角色（只保留本节点需要的角色）"
              value={node.character_names || []}
              onChange={(value) => updateNode(index, { character_names: value })}
            />
          </Card>
        ))}
        <div>
          <Text strong>设计说明</Text>
          <Input.TextArea
            value={designNotes}
            autoSize={{ minRows: 3, maxRows: 8 }}
            onChange={(event) => setDesignNotes(event.target.value)}
          />
        </div>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          “确认并写章”会先原子保存正式大纲，再以一条新的作者消息启动写章；不会在当前确认事务中暗中继续生成。
        </Paragraph>
        <Space wrap>
          <Button icon={<SaveOutlined />} loading={working === 'save'} onClick={() => void saveEdits()}>
            保存草稿修改
          </Button>
          <Button type="primary" loading={working === 'confirm'} onClick={() => void confirm(false)}>
            确认大纲
          </Button>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={working === 'confirm_and_write'}
            onClick={() => void confirm(true)}
          >
            确认并写章
          </Button>
          <Button icon={<ReloadOutlined />} loading={working === 'regenerate'} onClick={() => void regenerate()}>
            重新规划
          </Button>
          <Popconfirm
            title="丢弃这份大纲草稿？"
            okText="丢弃"
            cancelText="取消"
            onConfirm={() => void discard()}
          >
            <Button danger icon={<DeleteOutlined />} loading={working === 'discard'}>丢弃</Button>
          </Popconfirm>
        </Space>
      </Space>
    </Card>
  )
}

export default OutlineDraftReviewPanel
