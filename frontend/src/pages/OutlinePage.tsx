import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Collapse,
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
  PlusOutlined,
  ReloadOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { SaveStatusIndicator } from '../components/interaction'
import { useAiPanelContext } from '../contexts/AiPanelContext'
import { useUnsavedGuard } from '../hooks/useUnsavedGuard'
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
  metadata?: SceneMetadata
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
  metadata?: SceneMetadata
}

interface SceneMetadata {
  scene_number?: number
  purpose?: string
  location?: string
  timeline?: string
  pov_character?: string
  characters?: string[]
  entry_state?: string
  exit_state?: string
  emotional_residue?: string
  unresolved_actions?: string[]
}

interface OutlinePageProps {
  projectId: string
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

/** Build a tree from a flat list of outline nodes. */
function buildTree(flat: OutlineNode[]): OutlineNode[] {
  const map = new Map<string, OutlineNode>()
  const roots: OutlineNode[] = []
  // First pass: clone nodes with empty children
  flat.forEach((node) => {
    map.set(node.id, { ...node, children: [] })
  })
  // Second pass: attach children to parents
  flat.forEach((node) => {
    const clone = map.get(node.id)!
    if (node.parent_id && map.has(node.parent_id)) {
      map.get(node.parent_id)!.children.push(clone)
    } else {
      roots.push(clone)
    }
  })
  // Sort children by sort_order
  const sortChildren = (nodes: OutlineNode[]) => {
    nodes.sort((a, b) => a.sort_order - b.sort_order)
    nodes.forEach((n) => sortChildren(n.children))
  }
  sortChildren(roots)
  return roots
}

function nextChildType(parent?: OutlineNode | null): NodeType {
  if (!parent) return 'volume'
  if (parent.node_type === 'volume') return 'chapter'
  return 'section'
}

function OutlinePage({ projectId }: OutlinePageProps) {
  const [form] = Form.useForm<OutlineFormValues>()
  const watchedNodeType = Form.useWatch('node_type', form)
  const [tree, setTree] = useState<OutlineNode[]>([])
  const [flat, setFlat] = useState<OutlineNode[]>([])
  const [characters, setCharacters] = useState<CharacterItem[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [expandedKeys, setExpandedKeys] = useState<string[]>([])
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const { setAiContext, refreshKey } = useAiPanelContext()
  const {
    saveStatus,
    saveError,
    markDirty,
    markSaved,
    markSaving,
    markSaveFailed,
    confirmLeave,
  } = useUnsavedGuard()

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

  // Sync outline node selection to AI context
  useEffect(() => {
    setAiContext({ selectedOutlineNodeId: selectedId })
  }, [selectedId, setAiContext])

  // Refresh data when AI applies changes
  useEffect(() => {
    if (refreshKey > 0) {
      fetchOutline()
      fetchCharacters()
    }
  }, [fetchCharacters, fetchOutline, refreshKey])

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
        metadata: selectedNode.metadata || {},
      })
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
    confirmLeave(() => {
      setCreating(true)
      setSelectedId(null)
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
        metadata: {},
      })
    })
  }

  const saveOutlineNode = async (values: OutlineFormValues) => {
    if (!values.title.trim()) {
      message.warning('请输入节点标题')
      return
    }
    setSaving(true)
    markSaving()
    try {
      const payload = {
        parent_id: values.parent_id || null,
        node_type: values.node_type,
        title: values.title.trim(),
        summary: values.summary?.trim() || null,
        status: values.status,
        sort_order: Number(values.sort_order || 0),
        character_ids: values.character_ids || [],
        metadata: values.metadata || {},
      }

      if (creating || !selectedId) {
        const res = await apiClient.post<ApiResponse<OutlineNode>>(`/projects/${projectId}/outline`, payload)
        setSelectedId(res.data.data.id)
        setCreating(false)
      } else {
        const res = await apiClient.put<ApiResponse<OutlineNode>>(
          `/projects/${projectId}/outline/${selectedId}`,
          payload
        )
        setSelectedId(res.data.data.id)
      }
      markSaved()
      fetchOutline()
    } catch (err: any) {
      markSaveFailed(err.message || '保存大纲失败')
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

    // Save previous state for rollback
    const prevTree = tree
    const prevFlat = flat

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

      // Optimistic update: apply reorder locally before API call
      const updatedFlat = flat.map((node) => {
        const match = items.find((item) => item.id === node.id)
        if (match) {
          return { ...node, parent_id: match.parent_id, sort_order: match.sort_order }
        }
        return node
      })
      setFlat(updatedFlat)
      // Rebuild tree from updated flat list
      setTree(buildTree(updatedFlat))

      await apiClient.put(`/projects/${projectId}/outline/reorder`, { items })
      message.success('大纲顺序已更新')
      // Sync with server to get any computed fields
      fetchOutline()
    } catch (err: any) {
      // Rollback on failure
      setTree(prevTree)
      setFlat(prevFlat)
      message.error(err.message || '调整大纲顺序失败')
    }
  }

  const selectedCharacterNames = selectedNode?.linked_characters.map((item) => item.name).join('、') || ''
  const editorTitle = creating ? '新建大纲节点' : selectedNode?.title || '大纲节点'

  return (
    <div className="outline-page">
      <div className="outline-shell">
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
                confirmLeave(() => {
                  setCreating(false)
                  setSelectedId(keys.length > 0 ? String(keys[0]) : null)
                })
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
                <Space size={8} wrap>
                  <Text type="secondary">
                    {nodeTypeLabel(selectedNode.node_type)} · {statusLabel(selectedNode.status)}
                    {selectedCharacterNames ? ` · ${selectedCharacterNames}` : ''}
                  </Text>
                  <SaveStatusIndicator status={saveStatus} error={saveError} />
                </Space>
              )}
            </div>
            <Space>
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
            <Form form={form} layout="vertical" onFinish={saveOutlineNode} onValuesChange={markDirty}>
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

              {watchedNodeType === 'section' && (
                <Collapse
                  ghost
                  items={[{
                    key: 'scene-metadata',
                    label: '场景事件信息',
                    children: (
                      <>
                        <div className="outline-grid">
                          <Form.Item name={['metadata', 'scene_number']} label="场景序号"><InputNumber min={1} max={6} style={{ width: '100%' }} /></Form.Item>
                          <Form.Item name={['metadata', 'location']} label="地点"><Input placeholder="本场景发生地点" /></Form.Item>
                          <Form.Item name={['metadata', 'timeline']} label="时间"><Input placeholder="相对上一场景的时间位置" /></Form.Item>
                          <Form.Item name={['metadata', 'pov_character']} label="视角角色"><Input placeholder="本场景的主要视角" /></Form.Item>
                        </div>
                        <Form.Item name={['metadata', 'purpose']} label="场景目的"><Input.TextArea rows={3} placeholder="这一场必须改变什么" /></Form.Item>
                        <Form.Item name={['metadata', 'characters']} label="出场角色"><Select mode="tags" tokenSeparators={[',', '，', '、']} placeholder="输入角色名后回车" /></Form.Item>
                        <div className="outline-grid">
                          <Form.Item name={['metadata', 'entry_state']} label="入场状态"><Input.TextArea rows={3} /></Form.Item>
                          <Form.Item name={['metadata', 'exit_state']} label="离场状态"><Input.TextArea rows={3} /></Form.Item>
                        </div>
                        <Form.Item name={['metadata', 'emotional_residue']} label="情绪余波"><Input.TextArea rows={3} /></Form.Item>
                        <Form.Item name={['metadata', 'unresolved_actions']} label="未解决动作"><Select mode="tags" tokenSeparators={[',', '，']} placeholder="输入一项后回车" /></Form.Item>
                      </>
                    ),
                  }]}
                />
              )}
            </Form>
          )}

        </main>

      </div>
    </div>
  )
}

export default OutlinePage
