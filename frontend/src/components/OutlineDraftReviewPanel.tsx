import { useEffect, useRef, useState } from 'react'
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
import { createLatestRequestGate } from '../shared/latestRequest'

const { Paragraph, Text, Title } = Typography

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface OutlineDraftReviewPanelProps {
  projectId: string
  draft: GeneratedOutlineDraft
  onDirtyChange: (dirty: boolean) => void
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
  onDirtyChange,
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
  const [savedSnapshot, setSavedSnapshot] = useState(() => JSON.stringify({
    nodes: draft.nodes, designNotes: draft.designNotes,
  }))
  const [working, setWorking] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const requestGate = useRef(createLatestRequestGate<string>())
  const dirty = JSON.stringify({ nodes, designNotes }) !== savedSnapshot
  const dirtyRef = useRef(dirty)
  const workingRef = useRef(working)
  dirtyRef.current = dirty
  workingRef.current = working

  useEffect(() => () => requestGate.current.invalidate(), [])

  useEffect(() => { onDirtyChange(dirty) }, [dirty, onDirtyChange])

  useEffect(() => {
    if (dirtyRef.current || workingRef.current) return
    setNodes(draft.nodes)
    setDesignNotes(draft.designNotes)
    setSavedSnapshot(JSON.stringify({ nodes: draft.nodes, designNotes: draft.designNotes }))
  }, [draft.designNotes, draft.draftId, draft.nodes])

  const updateNode = (index: number, partial: Partial<GeneratedOutlineDraftNode>) => {
    setActionError(null)
    setNodes((current) => current.map((node, nodeIndex) => (
      nodeIndex === index ? { ...node, ...partial } : node
    )))
  }

  const persist = async (ownsRequest: () => boolean) => {
    const response = await apiClient.put<ApiResponse<Record<string, any>>>(
      `/projects/${projectId}/outline-drafts/${draft.draftId}`,
      {
        nodes,
        design_notes: designNotes,
      },
    )
    if (!ownsRequest()) return
    const stored = response.data.data
    if (!Array.isArray(stored.nodes)) throw new Error('大纲草稿保存结果不完整')
    const storedNodes = stored.nodes as GeneratedOutlineDraftNode[]
    const storedNotes = String(stored.design_notes || '')
    setNodes(storedNodes)
    setDesignNotes(storedNotes)
    setSavedSnapshot(JSON.stringify({ nodes: storedNodes, designNotes: storedNotes }))
    updateGeneratedOutlineDraft({ nodes: storedNodes, designNotes: storedNotes })
    return response.data.data
  }

  const saveEdits = async () => {
    if (workingRef.current || !dirty) return
    const request = requestGate.current.begin(draft.draftId)
    const ownsRequest = () => requestGate.current.isCurrent(request)
    setActionError(null)
    setWorking('save')
    workingRef.current = 'save'
    try {
      await persist(ownsRequest)
      if (ownsRequest()) message.success('草稿已保存，等待确认加入正式大纲')
    } catch (error: any) {
      if (ownsRequest()) setActionError(error.message || '保存大纲草稿失败')
    } finally {
      if (ownsRequest()) {
        workingRef.current = null
        setWorking(null)
      }
    }
  }

  const confirm = async (writeAfterConfirm: boolean) => {
    if (workingRef.current) return
    const request = requestGate.current.begin(draft.draftId)
    const ownsRequest = () => requestGate.current.isCurrent(request)
    setActionError(null)
    setWorking(writeAfterConfirm ? 'confirm_and_write' : 'confirm')
    workingRef.current = writeAfterConfirm ? 'confirm_and_write' : 'confirm'
    try {
      if (dirty) await persist(ownsRequest)
      if (!ownsRequest()) return
      const response = await apiClient.post<ApiResponse<{
        saved_outline_node_ids?: string[]
        next_author_request?: { message?: string }
      }>>(
        `/projects/${projectId}/outline-drafts/${draft.draftId}/confirm`,
        { write_after_confirm: writeAfterConfirm },
      )
      if (!ownsRequest()) return
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
      if (ownsRequest()) setActionError(error.message || '确认大纲失败')
    } finally {
      if (ownsRequest()) {
        workingRef.current = null
        setWorking(null)
      }
    }
  }

  const regenerate = async () => {
    if (workingRef.current) return
    const request = requestGate.current.begin(draft.draftId)
    const ownsRequest = () => requestGate.current.isCurrent(request)
    setActionError(null)
    setWorking('regenerate')
    workingRef.current = 'regenerate'
    try {
      const response = await apiClient.post<ApiResponse<{
        next_author_request?: { message?: string }
      }>>(`/projects/${projectId}/outline-drafts/${draft.draftId}/regenerate`)
      if (!ownsRequest()) return
      updateGeneratedOutlineDraft({ status: 'superseded' })
      triggerRefresh()
      const nextMessage = String(response.data.data.next_author_request?.message || '')
      if (nextMessage) {
        requestAuthorAgentTurn({ projectId, message: nextMessage })
        navigate(`/project/${encodeURIComponent(projectId)}?view=outline&assistant=open`)
      }
    } catch (error: any) {
      if (ownsRequest()) setActionError(error.message || '重新规划失败')
    } finally {
      if (ownsRequest()) {
        workingRef.current = null
        setWorking(null)
      }
    }
  }

  const discard = async () => {
    if (workingRef.current) return
    const request = requestGate.current.begin(draft.draftId)
    const ownsRequest = () => requestGate.current.isCurrent(request)
    setActionError(null)
    setWorking('discard')
    workingRef.current = 'discard'
    try {
      await apiClient.delete(`/projects/${projectId}/outline-drafts/${draft.draftId}`)
      if (!ownsRequest()) return
      updateGeneratedOutlineDraft({ status: 'discarded' })
      triggerRefresh()
      message.success('大纲草稿已丢弃')
    } catch (error: any) {
      if (ownsRequest()) setActionError(error.message || '丢弃大纲草稿失败')
    } finally {
      if (ownsRequest()) {
        workingRef.current = null
        setWorking(null)
      }
    }
  }

  if (draft.status !== 'pending') return null

  return (
    <Card
      size="small"
      className="outline-draft-review"
      title={(
        <Space wrap size={6}>
          <Title level={5} style={{ margin: 0 }}>AI 大纲草稿</Title>
          <Tag color={dirty ? 'orange' : 'green'}>
            {working === 'save' ? '保存中' : dirty ? '有未保存修改' : '草稿已保存'}
          </Tag>
          <Tag color="gold">待确认</Tag>
        </Space>
      )}
      extra={<Text type="secondary">{nodes.length} 个节点</Text>}
    >
      <Space wrap style={{ marginBottom: 12 }}>
        <Button
          icon={<SaveOutlined />}
          disabled={Boolean(working) || !dirty}
          loading={working === 'save'}
          onClick={() => void saveEdits()}
        >
          保存草稿修改
        </Button>
        <Button type="primary" disabled={Boolean(working)} loading={working === 'confirm'} onClick={() => void confirm(false)}>
          确认大纲
        </Button>
        <Button
          type="primary"
          icon={<ThunderboltOutlined />}
          disabled={Boolean(working)}
          loading={working === 'confirm_and_write'}
          onClick={() => void confirm(true)}
        >
          确认并写章
        </Button>
        <Button disabled={Boolean(working)} icon={<ReloadOutlined />} loading={working === 'regenerate'} onClick={() => void regenerate()}>
          重新规划
        </Button>
        <Popconfirm
          title="丢弃这份大纲草稿？"
          okText="丢弃"
          cancelText="取消"
          onConfirm={() => void discard()}
        >
          <Button disabled={Boolean(working)} danger icon={<DeleteOutlined />} loading={working === 'discard'}>丢弃</Button>
        </Popconfirm>
      </Space>
      {actionError && <Alert type="error" showIcon message={actionError} style={{ marginBottom: 12 }} />}
      <Alert
        type="info"
        showIcon
        message="草稿已保留，等待作者确认"
        description="修改后点“保存草稿修改”；点“确认大纲”才会加入正式大纲。点“确认并写章”还会发起写章任务。"
        style={{ marginBottom: 12 }}
      />
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {nodes.map((node, index) => (
          <Card key={`${draft.draftId}-node-${index}`} size="small" type="inner" title={`节点 ${index + 1}`}>
            <div className="outline-draft-node-fields">
              <Select
                aria-label={`草稿节点 ${index + 1} 类型`}
                value={node.node_type}
                disabled={Boolean(working)}
                options={NODE_TYPES}
                onChange={(value) => updateNode(index, { node_type: value })}
              />
              <Input
                aria-label={`草稿节点 ${index + 1} 标题`}
                value={node.title}
                disabled={Boolean(working)}
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
                disabled={Boolean(working)}
                onChange={(event) => updateNode(index, { parent_title: event.target.value || null })}
              />
            )}
            <Input.TextArea
              style={{ marginTop: 8 }}
              aria-label={`草稿节点 ${index + 1} 摘要`}
              value={node.summary || ''}
              disabled={Boolean(working)}
              autoSize={{ minRows: 3, maxRows: 8 }}
              onChange={(event) => updateNode(index, { summary: event.target.value })}
            />
            <Select
              mode="tags"
              style={{ width: '100%', marginTop: 8 }}
              aria-label={`草稿节点 ${index + 1} 角色`}
              placeholder="涉及角色（只保留本节点需要的角色）"
              value={node.character_names || []}
              disabled={Boolean(working)}
              onChange={(value) => updateNode(index, { character_names: value })}
            />
          </Card>
        ))}
        <div>
          <Text strong>设计说明</Text>
          <Input.TextArea
            value={designNotes}
            disabled={Boolean(working)}
            autoSize={{ minRows: 3, maxRows: 8 }}
            onChange={(event) => {
              setActionError(null)
              setDesignNotes(event.target.value)
            }}
          />
        </div>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          “确认并写章”会先原子保存正式大纲，再以一条新的作者消息启动写章；不会在当前确认事务中暗中继续生成。
        </Paragraph>
      </Space>
    </Card>
  )
}

export default OutlineDraftReviewPanel
