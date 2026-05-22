import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Empty,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Select,
  Space,
  Tag,
  Tooltip,
  Tree,
  Typography,
  message,
} from 'antd'
import type { DataNode, TreeProps } from 'antd/es/tree'
import {
  BranchesOutlined,
  DeleteOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import WorkspaceAssistantChat from '../components/WorkspaceAssistantChat'
import { useModelOptions } from '../hooks/useModelOptions'
import { usePanelResize } from '../hooks/usePanelResize'
import './OutlinePage.css'

const { Text, Title } = Typography

type NodeType = 'volume' | 'chapter' | 'section'
type NodeStatus = 'pending' | 'in_progress' | 'completed'

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface LinkedCharacter {
  id: string
  name: string
  role_type?: string
  role_in_scene?: string
}

interface OutlineNode {
  id: string
  project_id: string
  parent_id?: string | null
  node_type: NodeType
  title: string
  summary?: string
  status: NodeStatus
  sort_order: number
  linked_characters: LinkedCharacter[]
  children: OutlineNode[]
  created_at: string
  updated_at: string
}

interface OutlineListResponse {
  items: OutlineNode[]
  flat: OutlineNode[]
  total: number
}

interface CharacterItem {
  id: string
  name: string
  role_type?: string
}

interface OutlineFormValues {
  parent_id?: string | null
  node_type: NodeType
  title: string
  summary?: string
  status: NodeStatus
  sort_order: number
  character_ids: string[]
}

interface OutlinePageProps {
  projectId: string
}

interface AIRunLog {
  key: string
  tool?: string
  status?: string
  message: string
}

const NODE_TYPE_OPTIONS: Array<{ value: NodeType; label: string }> = [
  { value: 'volume', label: '卷' },
  { value: 'chapter', label: '章' },
  { value: 'section', label: '节' },
]

const STATUS_OPTIONS: Array<{ value: NodeStatus; label: string }> = [
  { value: 'pending', label: '待规划' },
  { value: 'in_progress', label: '进行中' },
  { value: 'completed', label: '已完成' },
]

const NODE_TYPE_COLOR: Record<NodeType, string> = {
  volume: 'geekblue',
  chapter: 'green',
  section: 'orange',
}

const STATUS_COLOR: Record<NodeStatus, string> = {
  pending: 'default',
  in_progress: 'processing',
  completed: 'success',
}

const nodeTypeLabel = (type: NodeType) => NODE_TYPE_OPTIONS.find((item) => item.value === type)?.label || type
const statusLabel = (status: NodeStatus) => STATUS_OPTIONS.find((item) => item.value === status)?.label || status

function collectTreeKeys(nodes: OutlineNode[]): string[] {
  return nodes.flatMap((node) => [node.id, ...collectTreeKeys(node.children || [])])
}

function collectDescendantIds(node?: OutlineNode | null): Set<string> {
  const ids = new Set<string>()
  const walk = (items: OutlineNode[]) => {
    items.forEach((item) => {
      ids.add(item.id)
      walk(item.children || [])
    })
  }
  if (node) walk(node.children || [])
  return ids
}

function nextChildType(parent?: OutlineNode | null): NodeType {
  if (!parent) return 'volume'
  if (parent.node_type === 'volume') return 'chapter'
  return 'section'
}

function OutlinePage({ projectId }: OutlinePageProps) {
  const [form] = Form.useForm<OutlineFormValues>()
  const [tree, setTree] = useState<OutlineNode[]>([])
  const [flat, setFlat] = useState<OutlineNode[]>([])
  const [characters, setCharacters] = useState<CharacterItem[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [expandedKeys, setExpandedKeys] = useState<string[]>([])
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [aiPrompt, setAiPrompt] = useState('')
  const [aiSuggestion, setAiSuggestion] = useState('')
  const [aiSuggestedNodes, setAiSuggestedNodes] = useState<Array<{ title: string; summary: string; node_type?: NodeType; character_names?: string[] }>>([])
  const [aiLoading, setAiLoading] = useState(false)
  const [aiModel, setAiModel] = useState<string | undefined>()
  const [aiSuggestionCount, setAiSuggestionCount] = useState(4)
  const [aiPanelCollapsed, setAiPanelCollapsed] = useState(false)
  const [aiRunLogs, setAiRunLogs] = useState<AIRunLog[]>([])
  const { modelOptions, defaultModel, loading: modelsLoading } = useModelOptions()
  const { width: aiPanelWidth, onDragHandleMouseDown: onAiPanelDrag, dragging: aiPanelDragging } = usePanelResize({
    initialWidth: Math.min(620, Math.max(300, window.innerWidth * 0.24)),
  })

  const selectedNode = useMemo(
    () => flat.find((node) => node.id === selectedId) || null,
    [flat, selectedId]
  )
  const blockedParentIds = useMemo(() => collectDescendantIds(selectedNode), [selectedNode])

  const fetchOutline = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<OutlineListResponse>>(`/projects/${projectId}/outline`)
      const payload = res.data.data
      setTree(payload.items)
      setFlat(payload.flat)
      setExpandedKeys((keys) => (keys.length > 0 ? keys : collectTreeKeys(payload.items)))

      if (selectedId && !payload.flat.some((node) => node.id === selectedId)) {
        setSelectedId(payload.flat[0]?.id || null)
      } else if (!selectedId && !creating && payload.flat.length > 0) {
        setSelectedId(payload.flat[0].id)
      }
    } catch (err: any) {
      message.error(err.message || '获取大纲失败')
    } finally {
      setLoading(false)
    }
  }, [creating, projectId, selectedId])

  const fetchCharacters = useCallback(async () => {
    try {
      const res = await apiClient.get<ApiResponse<{ items: CharacterItem[]; total: number }>>(
        `/projects/${projectId}/characters`
      )
      setCharacters(res.data.data.items)
    } catch (err: any) {
      message.error(err.message || '获取角色失败')
    }
  }, [projectId])

  useEffect(() => {
    fetchOutline()
    fetchCharacters()
  }, [fetchCharacters, fetchOutline])

  useEffect(() => {
    if (!aiModel && defaultModel) {
      setAiModel(defaultModel)
    }
  }, [aiModel, defaultModel])

  useEffect(() => {
    if (!creating && selectedNode) {
      form.setFieldsValue({
        parent_id: selectedNode.parent_id || undefined,
        node_type: selectedNode.node_type,
        title: selectedNode.title,
        summary: selectedNode.summary || '',
        status: selectedNode.status,
        sort_order: selectedNode.sort_order,
        character_ids: selectedNode.linked_characters.map((item) => item.id),
      })
      setAiSuggestion('')
    }
    if (!creating && !selectedNode) {
      form.resetFields()
    }
  }, [creating, form, selectedNode])

  const parentOptions = useMemo(
    () =>
      flat
        .filter((node) => node.id !== selectedId && !blockedParentIds.has(node.id))
        .map((node) => ({
          value: node.id,
          label: `${nodeTypeLabel(node.node_type)} · ${node.title}`,
        })),
    [blockedParentIds, flat, selectedId]
  )

  const characterOptions = useMemo(
    () =>
      characters.map((character) => ({
        value: character.id,
        label: character.role_type ? `${character.name} · ${character.role_type}` : character.name,
      })),
    [characters]
  )

  const treeData = useMemo<DataNode[]>(() => {
    const renderNode = (node: OutlineNode): DataNode => ({
      key: node.id,
      title: (
        <div className="outline-tree-title">
          <span className="outline-tree-main">
            {node.node_type === 'volume' ? <FolderOpenOutlined /> : <FileTextOutlined />}
            <span title={node.title}>{node.title}</span>
          </span>
          <span className="outline-tree-tags">
            <Tag color={NODE_TYPE_COLOR[node.node_type]}>{nodeTypeLabel(node.node_type)}</Tag>
            <Tag color={STATUS_COLOR[node.status]}>{statusLabel(node.status)}</Tag>
          </span>
        </div>
      ),
      children: node.children.map(renderNode),
    })
    return tree.map(renderNode)
  }, [tree])

  const startCreate = (parent?: OutlineNode | null) => {
    setCreating(true)
    setSelectedId(null)
    setAiSuggestion('')
    setAiSuggestedNodes([])
    const parentId = parent?.id || null
    const siblingCount = flat.filter((node) => (node.parent_id || null) === parentId).length
    form.setFieldsValue({
      parent_id: parentId || undefined,
      node_type: nextChildType(parent),
      title: '',
      summary: '',
      status: 'pending',
      sort_order: siblingCount,
      character_ids: [],
    })
  }

  const saveOutlineNode = async (values: OutlineFormValues) => {
    setSaving(true)
    try {
      const payload = {
        parent_id: values.parent_id || null,
        node_type: values.node_type,
        title: values.title.trim(),
        summary: values.summary?.trim() || null,
        status: values.status,
        sort_order: Number(values.sort_order || 0),
        character_ids: values.character_ids || [],
      }

      if (!payload.title) {
        message.warning('请输入节点标题')
        return
      }

      if (creating || !selectedId) {
        const res = await apiClient.post<ApiResponse<OutlineNode>>(`/projects/${projectId}/outline`, payload)
        setSelectedId(res.data.data.id)
        setCreating(false)
        message.success('大纲节点已创建')
      } else {
        const res = await apiClient.put<ApiResponse<OutlineNode>>(
          `/projects/${projectId}/outline/${selectedId}`,
          payload
        )
        setSelectedId(res.data.data.id)
        message.success('大纲节点已保存')
      }
      fetchOutline()
    } catch (err: any) {
      message.error(err.message || '保存大纲失败')
    } finally {
      setSaving(false)
    }
  }

  const deleteSelected = async () => {
    if (!selectedId) return
    try {
      await apiClient.delete(`/projects/${projectId}/outline/${selectedId}`)
      message.success('大纲节点已删除')
      setSelectedId(null)
      setCreating(false)
      fetchOutline()
    } catch (err: any) {
      message.error(err.message || '删除大纲失败')
    }
  }

  const handleDrop: TreeProps['onDrop'] = async (info) => {
    const draggedId = String(info.dragNode.key)
    const targetId = String(info.node.key)
    if (draggedId === targetId) return

    const draggedNode = flat.find((node) => node.id === draggedId)
    const targetNode = flat.find((node) => node.id === targetId)
    if (!draggedNode || !targetNode) return

    try {
      let items: Array<{ id: string; parent_id: string | null; sort_order: number }> = []
      if (!info.dropToGap) {
        const parentId = targetNode.id
        const children = targetNode.children.filter((node) => node.id !== draggedId)
        const nextChildren = [...children, { ...draggedNode, parent_id: parentId }]
        items = nextChildren.map((node, index) => ({
          id: node.id,
          parent_id: parentId,
          sort_order: index,
        }))
        setExpandedKeys((keys) => Array.from(new Set([...keys, parentId])))
      } else {
        const parentId = targetNode.parent_id || null
        const siblings = flat
          .filter((node) => (node.parent_id || null) === parentId && node.id !== draggedId)
          .sort((a, b) => a.sort_order - b.sort_order)
        const nodeWithPos = info.node as typeof info.node & { pos?: string }
        const posParts = nodeWithPos.pos?.split('-') || []
        const relativeDropPosition = info.dropPosition - Number(posParts[posParts.length - 1] || 0)
        const targetIndex = Math.max(0, siblings.findIndex((node) => node.id === targetId))
        const insertIndex = relativeDropPosition > 0 ? targetIndex + 1 : targetIndex
        siblings.splice(insertIndex, 0, { ...draggedNode, parent_id: parentId })
        items = siblings.map((node, index) => ({
          id: node.id,
          parent_id: parentId,
          sort_order: index,
        }))
      }

      await apiClient.put(`/projects/${projectId}/outline/reorder`, { items })
      message.success('大纲顺序已更新')
      fetchOutline()
    } catch (err: any) {
      message.error(err.message || '调整大纲顺序失败')
    }
  }

  const generateAISuggestion = async () => {
    setAiLoading(true)
    setAiSuggestion('')
    setAiSuggestedNodes([])
    setAiRunLogs([{
      key: `${Date.now()}-start`,
      tool: 'outline_context',
      status: 'running',
      message: '正在读取当前大纲、同级节点、角色和世界观',
    }])
    try {
      setAiRunLogs((prev) => [...prev, {
        key: `${Date.now()}-model`,
        tool: 'outline_ai',
        status: 'running',
        message: `正在调用模型：${aiModel || defaultModel || '默认模型'}`,
      }])
      const res = await apiClient.post<ApiResponse<{ suggestion: string; suggestions?: Array<{ title: string; summary: string; node_type?: NodeType; character_names?: string[] }> }>>(
        `/projects/${projectId}/outline/ai-suggest`,
        {
          node_id: selectedId || undefined,
          prompt: aiPrompt.trim() || undefined,
          suggestion_count: aiSuggestionCount,
          model: aiModel || defaultModel || undefined,
        }
      )
      setAiSuggestion(res.data.data.suggestion)
      setAiSuggestedNodes(res.data.data.suggestions || [])
      setAiRunLogs((prev) => [...prev, {
        key: `${Date.now()}-done`,
        tool: 'outline_ai',
        status: 'ok',
        message: `已生成 ${res.data.data.suggestions?.length || 0} 个连续大纲建议`,
      }])
    } catch (err: any) {
      setAiRunLogs((prev) => [...prev, {
        key: `${Date.now()}-error`,
        tool: 'outline_ai',
        status: 'error',
        message: err.message || 'AI 摘要建议失败',
      }])
      message.error(err.message || 'AI 摘要建议失败，请检查模型配置')
    } finally {
      setAiLoading(false)
    }
  }

  const fillAISuggestion = () => {
    if (!aiSuggestion.trim()) return
    form.setFieldValue('summary', aiSuggestion.trim())
  }

  const createSuggestedNodes = async () => {
    if (aiSuggestedNodes.length === 0) return
    const targetParentId = selectedNode
      ? (selectedNode.node_type === 'volume' ? selectedNode.id : selectedNode.parent_id || null)
      : null
    const baseSort = flat.filter((node) => (node.parent_id || null) === targetParentId).length
    try {
      for (const [index, item] of aiSuggestedNodes.entries()) {
        const characterIds = (item.character_names || [])
          .map((name) => characters.find((character) => character.name === name)?.id)
          .filter(Boolean) as string[]
        await apiClient.post<ApiResponse<OutlineNode>>(`/projects/${projectId}/outline`, {
          parent_id: targetParentId,
          node_type: item.node_type || (selectedNode?.node_type === 'volume' ? 'chapter' : selectedNode?.node_type || 'chapter'),
          title: item.title,
          summary: item.summary,
          status: 'pending',
          sort_order: baseSort + index,
          character_ids: characterIds,
        })
      }
      message.success(`已创建 ${aiSuggestedNodes.length} 个连续大纲节点`)
      setAiSuggestedNodes([])
      fetchOutline()
    } catch (err: any) {
      message.error(err.message || '创建建议大纲失败')
    }
  }

  const selectedCharacterNames = selectedNode?.linked_characters.map((item) => item.name).join('、') || ''
  const editorTitle = creating ? '新建大纲节点' : selectedNode?.title || '大纲节点'
  void aiLoading
  void aiRunLogs
  void setAiPrompt
  void setAiSuggestionCount
  void generateAISuggestion
  void fillAISuggestion
  void createSuggestedNodes

  return (
    <div className="outline-page">
      <div className={`outline-shell${aiPanelCollapsed ? ' outline-shell-ai-collapsed' : ''}`}>
        <aside className="outline-tree-panel">
          <div className="outline-panel-head">
            <Title level={4} style={{ margin: 0 }}>
              <BranchesOutlined /> 大纲
            </Title>
            <Space size={6}>
              <Tooltip title="刷新">
                <Button icon={<ReloadOutlined />} onClick={fetchOutline} loading={loading} />
              </Tooltip>
              <Tooltip title="新增根节点">
                <Button icon={<PlusOutlined />} onClick={() => startCreate(null)} />
              </Tooltip>
              <Tooltip title="新增子节点">
                <Button icon={<FileTextOutlined />} disabled={!selectedNode} onClick={() => startCreate(selectedNode)} />
              </Tooltip>
            </Space>
          </div>

          {treeData.length === 0 && !loading ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无大纲">
              <Button type="primary" icon={<PlusOutlined />} onClick={() => startCreate(null)}>
                新增节点
              </Button>
            </Empty>
          ) : (
            <Tree
              blockNode
              draggable
              treeData={treeData}
              selectedKeys={selectedId ? [selectedId] : []}
              expandedKeys={expandedKeys}
              onExpand={(keys) => setExpandedKeys(keys.map(String))}
              onSelect={(keys) => {
                setCreating(false)
                setSelectedId(keys.length > 0 ? String(keys[0]) : null)
              }}
              onDrop={handleDrop}
            />
          )}
        </aside>

        <main className="outline-editor">
          <div className="outline-editor-head">
            <div>
              <Title level={4} style={{ margin: 0 }}>
                {editorTitle}
              </Title>
              {!creating && selectedNode && (
                <Text type="secondary">
                  {nodeTypeLabel(selectedNode.node_type)} · {statusLabel(selectedNode.status)}
                  {selectedCharacterNames ? ` · ${selectedCharacterNames}` : ''}
                </Text>
              )}
            </div>
            <Space>
              {aiPanelCollapsed && (
                <Button icon={<MenuUnfoldOutlined />} onClick={() => setAiPanelCollapsed(false)}>
                  AI 辅助
                </Button>
              )}
              {!creating && selectedId && (
                <Popconfirm
                  title="删除大纲节点"
                  description="子节点和关联角色记录也会一并删除。"
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true, autoInsertSpace: false }}
                  cancelButtonProps={{ autoInsertSpace: false }}
                  onConfirm={deleteSelected}
                >
                  <Button danger icon={<DeleteOutlined />}>
                    删除
                  </Button>
                </Popconfirm>
              )}
              <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => form.submit()}>
                保存
              </Button>
            </Space>
          </div>

          {!creating && !selectedNode && tree.length === 0 ? (
            <Alert type="info" showIcon message="先创建一个大纲节点" />
          ) : (
            <Form form={form} layout="vertical" onFinish={saveOutlineNode}>
              <div className="outline-grid">
                <Form.Item name="parent_id" label="父级节点">
                  <Select
                    allowClear
                    placeholder="无父级"
                    options={parentOptions}
                    showSearch
                    optionFilterProp="label"
                  />
                </Form.Item>
                <Form.Item name="node_type" label="节点类型" rules={[{ required: true, message: '请选择节点类型' }]}>
                  <Select options={NODE_TYPE_OPTIONS} />
                </Form.Item>
                <Form.Item name="status" label="状态" rules={[{ required: true, message: '请选择状态' }]}>
                  <Select options={STATUS_OPTIONS} />
                </Form.Item>
                <Form.Item name="sort_order" label="排序">
                  <InputNumber min={0} style={{ width: '100%' }} />
                </Form.Item>
              </div>

              <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
                <Input placeholder="例如：边城风祭" maxLength={200} />
              </Form.Item>

              <Form.Item name="summary" label="摘要">
                <Input.TextArea
                  placeholder="冲突、行动、转折、悬念"
                  autoSize={{ minRows: 7, maxRows: 14 }}
                  showCount
                />
              </Form.Item>

              <Form.Item name="character_ids" label="关联角色">
                <Select
                  mode="multiple"
                  allowClear
                  options={characterOptions}
                  placeholder="选择本节点涉及的角色"
                  optionFilterProp="label"
                />
              </Form.Item>
            </Form>
          )}

        </main>

        {!aiPanelCollapsed && (
          <aside className={`outline-ai-panel${aiPanelDragging ? ' outline-ai-panel-dragging' : ''}`} style={{ width: aiPanelWidth }}>
            <div className="outline-ai-resize-handle" onMouseDown={onAiPanelDrag} />
            <div className="outline-ai-head">
              <Title level={5} style={{ margin: 0 }}>
                <RobotOutlined /> 项目助手
              </Title>
              <Button type="text" size="small" icon={<MenuFoldOutlined />} onClick={() => setAiPanelCollapsed(true)} />
            </div>
            <WorkspaceAssistantChat
              projectId={projectId}
              scope="project"
              selectedOutlineNodeId={selectedId}
              model={aiModel}
              defaultModel={defaultModel}
              modelOptions={modelOptions}
              modelsLoading={modelsLoading}
              onModelChange={setAiModel}
              onApplied={() => {
                fetchOutline()
                fetchCharacters()
              }}
            />
          </aside>
        )}
      </div>
    </div>
  )
}

export default OutlinePage
