import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
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
  MinusSquareOutlined,
  PlusOutlined,
  PlusSquareOutlined,
  ReloadOutlined,
  SaveOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { SaveStatusIndicator } from '../components/interaction'
import OutlineDraftReviewPanel from '../components/OutlineDraftReviewPanel'
import {
  useAiPanelContext,
  type GeneratedOutlineDraft,
  type GeneratedOutlineDraftNode,
} from '../contexts/AiPanelContext'
import { useUnsavedGuard } from '../hooks/useUnsavedGuard'
import { createLatestRequestGate } from '../shared/latestRequest'
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

function outlineDraftFromPayload(
  payload: Record<string, any>,
  projectId: string,
): GeneratedOutlineDraft | null {
  const draftId = String(payload.draft_id || '')
  const nodes = Array.isArray(payload.nodes)
    ? payload.nodes.filter((node: unknown): node is GeneratedOutlineDraftNode => (
      Boolean(node && typeof node === 'object' && 'title' in node && 'node_type' in node)
    ))
    : []
  if (!draftId || nodes.length === 0) return null
  return {
    draftId,
    projectId: String(payload.project_id || projectId),
    contextManifestId: payload.context_manifest_id ? String(payload.context_manifest_id) : null,
    parentId: payload.parent_id ? String(payload.parent_id) : null,
    insertAfterId: payload.insert_after_id ? String(payload.insert_after_id) : null,
    status: String(payload.draft_status || 'pending') as GeneratedOutlineDraft['status'],
    nodes,
    designNotes: String(payload.design_notes || ''),
    savedOutlineNodeIds: Array.isArray(payload.saved_outline_node_ids)
      ? payload.saved_outline_node_ids.map(String)
      : [],
  }
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

const nodeTypeLabel = (type: NodeType) => NODE_TYPE_OPTIONS.find((item) => item.value === type)?.label || type
const statusLabel = (status: NodeStatus) => STATUS_OPTIONS.find((item) => item.value === status)?.label || status

export function collectTreeKeys(nodes: OutlineNode[]): string[] {
  return nodes.flatMap((node) => [node.id, ...collectTreeKeys(node.children || [])])
}

export function collectSelectedPath(nodes: OutlineNode[], selectedId?: string | null): string[] {
  if (!selectedId) return nodes.map((node) => node.id)
  const visit = (items: OutlineNode[], path: string[]): string[] | null => {
    for (const node of items) {
      const nextPath = [...path, node.id]
      if (node.id === selectedId) return nextPath
      const nested = visit(node.children || [], nextPath)
      if (nested) return nested
    }
    return null
  }
  return visit(nodes, []) || nodes.map((node) => node.id)
}

function filterOutlineTree(nodes: OutlineNode[], predicate: (node: OutlineNode) => boolean): OutlineNode[] {
  return nodes.flatMap((node) => {
    const children = filterOutlineTree(node.children || [], predicate)
    return predicate(node) || children.length > 0 ? [{ ...node, children }] : []
  })
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

function insertDraftRoots(
  siblings: DataNode[],
  draftRoots: DataNode[],
  insertAfterId: string | null,
): DataNode[] {
  if (draftRoots.length === 0) return siblings
  const index = insertAfterId
    ? siblings.findIndex((node) => String(node.key) === insertAfterId)
    : -1
  const insertionIndex = index >= 0 ? index + 1 : siblings.length
  return [
    ...siblings.slice(0, insertionIndex),
    ...draftRoots,
    ...siblings.slice(insertionIndex),
  ]
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
  const selectedIdRef = useRef<string | null>(null)
  const [expandedKeys, setExpandedKeys] = useState<string[]>([])
  const [searchKeyword, setSearchKeyword] = useState('')
  const [typeFilter, setTypeFilter] = useState<NodeType | 'all'>('all')
  const [statusFilter, setStatusFilter] = useState<NodeStatus | 'all'>('all')
  const [panelWidth, setPanelWidth] = useState(() => {
    const stored = Number(localStorage.getItem('siming_outline_panel_width'))
    return Number.isFinite(stored) && stored >= 320 && stored <= 480 ? stored : 380
  })
  const [resizing, setResizing] = useState(false)
  const resizeStartX = useRef(0)
  const resizeStartWidth = useRef(panelWidth)
  const [creating, setCreating] = useState(false)
  const creatingRef = useRef(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const formRevisionRef = useRef(0)
  const saveRequestGate = useRef(createLatestRequestGate<string>())
  const skipNextFormLoadRef = useRef<string | null>(null)
  const {
    refreshKey,
    generatedOutlineDraft,
    openGeneratedOutlineDraft,
  } = useAiPanelContext()
  const pendingOutlineDraft = (
    generatedOutlineDraft?.projectId === projectId
    && generatedOutlineDraft.status === 'pending'
  ) ? generatedOutlineDraft : null
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
  const filterActive = Boolean(searchKeyword.trim() || typeFilter !== 'all' || statusFilter !== 'all')
  const filteredTree = useMemo(() => {
    const keyword = searchKeyword.trim().toLocaleLowerCase('zh-CN')
    return filterOutlineTree(tree, (node) => (
      (!keyword || `${node.title} ${node.summary || ''}`.toLocaleLowerCase('zh-CN').includes(keyword))
      && (typeFilter === 'all' || node.node_type === typeFilter)
      && (statusFilter === 'all' || node.status === statusFilter)
    ))
  }, [searchKeyword, statusFilter, tree, typeFilter])

  const updatePanelWidth = (nextWidth: number) => {
    const clampedWidth = Math.min(480, Math.max(320, nextWidth))
    localStorage.setItem('siming_outline_panel_width', String(clampedWidth))
    setPanelWidth(clampedWidth)
  }

  const fetchOutline = useCallback(async (preferredSelectedId?: string) => {
    setLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<OutlineListResponse>>(`/projects/${projectId}/outline`)
      const payload = res.data.data
      setTree(payload.items)
      setFlat(payload.flat)
      const currentSelectedId = preferredSelectedId || selectedIdRef.current
      const nextSelectedId = currentSelectedId && payload.flat.some((node) => node.id === currentSelectedId)
        ? currentSelectedId
        : (!creatingRef.current ? payload.flat[0]?.id || null : null)
      setExpandedKeys((keys) => (keys.length > 0 ? keys : collectSelectedPath(payload.items, nextSelectedId)))
      if (nextSelectedId !== selectedIdRef.current) {
        selectedIdRef.current = nextSelectedId
        setSelectedId(nextSelectedId)
      }
    } catch (err: any) {
      message.error(err.message || '获取大纲失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

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

  const fetchPendingOutlineDraft = useCallback(async () => {
    try {
      const response = await apiClient.get<ApiResponse<Record<string, any> | null>>(
        `/projects/${projectId}/outline-drafts/pending`,
      )
      const pending = response.data.data
        ? outlineDraftFromPayload(response.data.data, projectId)
        : null
      if (pending) openGeneratedOutlineDraft(pending)
    } catch (err: any) {
      message.error(err.message || '获取大纲草稿失败')
    }
  }, [openGeneratedOutlineDraft, projectId])

  useEffect(() => {
    fetchOutline()
    fetchCharacters()
    fetchPendingOutlineDraft()
  }, [fetchCharacters, fetchOutline, fetchPendingOutlineDraft])

  // Refresh data when AI applies changes
  useEffect(() => {
    if (refreshKey > 0) {
      fetchOutline()
      fetchCharacters()
      fetchPendingOutlineDraft()
    }
  }, [fetchCharacters, fetchOutline, fetchPendingOutlineDraft, refreshKey])

  useEffect(() => {
    if (!creating && selectedNode) {
      if (skipNextFormLoadRef.current === selectedNode.id) {
        skipNextFormLoadRef.current = null
        return
      }
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
      if (skipNextFormLoadRef.current) return
      form.resetFields()
    }
  }, [creating, form, selectedNode])

  const visibleExpandedKeys = useMemo(() => {
    const keys = filterActive ? collectTreeKeys(filteredTree) : expandedKeys
    const parentId = pendingOutlineDraft?.parentId
    return parentId && !keys.includes(parentId) ? [...keys, parentId] : keys
  }, [expandedKeys, filterActive, filteredTree, pendingOutlineDraft?.parentId])

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
          <span className="outline-tree-meta" title={`${nodeTypeLabel(node.node_type)} · ${statusLabel(node.status)}`}>
            <span>{nodeTypeLabel(node.node_type)}</span>
            <span className={`outline-status-dot outline-status-dot-${node.status}`} aria-label={statusLabel(node.status)} />
          </span>
        </div>
      ),
      children: node.children.map(renderNode),
    })
    const formal = filteredTree.map(renderNode)
    if (!pendingOutlineDraft) return formal

    const draftByTitle = new Map<string, DataNode>()
    const draftRoots: DataNode[] = []
    pendingOutlineDraft.nodes.forEach((node, index) => {
      const dataNode: DataNode = {
        key: `outline-draft-${pendingOutlineDraft.draftId}-${index}`,
        className: 'outline-tree-draft-node',
        selectable: false,
        title: (
          <div className="outline-tree-title outline-tree-title-draft">
            <span className="outline-tree-main">
              <FileTextOutlined />
              <span title={node.title}>{node.title}</span>
            </span>
            <span className="outline-tree-meta">
              <Tag color="gold">未保存</Tag>
            </span>
          </div>
        ),
        children: [],
      }
      draftByTitle.set(node.title, dataNode)
      const parentTitle = String(node.parent_title || '')
      const draftParent = parentTitle ? draftByTitle.get(parentTitle) : null
      if (draftParent) {
        draftParent.children = [...(draftParent.children || []), dataNode]
      } else {
        draftRoots.push(dataNode)
      }
    })

    const inject = (items: DataNode[]): DataNode[] => {
      const copied = items.map((item) => ({
        ...item,
        children: item.children ? inject(item.children) : [],
      }))
      if (!pendingOutlineDraft.parentId) return copied
      return copied.map((item) => (
        String(item.key) === pendingOutlineDraft.parentId
          ? { ...item, children: insertDraftRoots(item.children || [], draftRoots, pendingOutlineDraft.insertAfterId) }
          : item
      ))
    }
    if (pendingOutlineDraft.parentId) return inject(formal)
    return insertDraftRoots(formal, draftRoots, pendingOutlineDraft.insertAfterId)
  }, [filteredTree, pendingOutlineDraft])

  const startCreate = (parent?: OutlineNode | null) => {
    confirmLeave(() => {
      saveRequestGate.current.invalidate()
      setSaving(false)
      creatingRef.current = true
      selectedIdRef.current = null
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
    const targetCreating = creatingRef.current || !selectedIdRef.current
    const targetId = selectedIdRef.current
    const targetKey = targetCreating ? 'create' : `view:${targetId}`
    const request = saveRequestGate.current.begin(targetKey)
    const submittedRevision = formRevisionRef.current
    const ownsTarget = () => (
      saveRequestGate.current.isCurrent(request)
      && (targetCreating
        ? creatingRef.current
        : !creatingRef.current && selectedIdRef.current === targetId)
    )
    const ownsSnapshot = () => ownsTarget() && formRevisionRef.current === submittedRevision
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

      if (targetCreating) {
        const res = await apiClient.post<ApiResponse<OutlineNode>>(`/projects/${projectId}/outline`, payload)
        if (ownsTarget()) {
          const created = res.data.data
          const snapshotStillCurrent = ownsSnapshot()
          creatingRef.current = false
          selectedIdRef.current = created.id
          skipNextFormLoadRef.current = snapshotStillCurrent ? null : created.id
          setSelectedId(created.id)
          setCreating(false)
          if (snapshotStillCurrent) markSaved()
          else markDirty()
          void fetchOutline(created.id)
        } else {
          void fetchOutline()
        }
      } else {
        const res = await apiClient.put<ApiResponse<OutlineNode>>(
          `/projects/${projectId}/outline/${targetId}`,
          payload
        )
        if (ownsTarget()) {
          const snapshotStillCurrent = ownsSnapshot()
          selectedIdRef.current = res.data.data.id
          skipNextFormLoadRef.current = snapshotStillCurrent ? null : res.data.data.id
          setSelectedId(res.data.data.id)
          if (snapshotStillCurrent) markSaved()
          else markDirty()
          void fetchOutline(res.data.data.id)
        } else {
          void fetchOutline()
        }
      }
    } catch (err: any) {
      if (ownsTarget()) {
        const detail = err.message || '保存大纲失败'
        markSaveFailed(detail)
        message.error(detail)
      }
    } finally {
      if (saveRequestGate.current.isCurrent(request)) setSaving(false)
    }
  }

  const deleteSelected = async () => {
    if (!selectedId) return
    try {
      await apiClient.delete(`/projects/${projectId}/outline/${selectedId}`)
      message.success('大纲节点已删除')
      saveRequestGate.current.invalidate()
      selectedIdRef.current = null
      creatingRef.current = false
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
      <div
        className={`outline-shell${resizing ? ' outline-shell-resizing' : ''}`}
        style={{ '--outline-panel-width': `${panelWidth}px` } as CSSProperties}
      >
        <aside className="outline-tree-panel">
          <div className="outline-panel-head">
            <Title level={4} style={{ margin: 0 }}>
              <BranchesOutlined /> 大纲
            </Title>
            <Space size={6}>
              <Tooltip title="刷新">
                <Button
                  aria-label="刷新大纲"
                  icon={<ReloadOutlined />}
                  onClick={() => void fetchOutline()}
                  loading={loading}
                />
              </Tooltip>
              <Tooltip title="新增根节点">
                <Button icon={<PlusOutlined />} onClick={() => startCreate(null)} />
              </Tooltip>
              <Tooltip title="新增子节点">
                <Button icon={<FileTextOutlined />} disabled={!selectedNode} onClick={() => startCreate(selectedNode)} />
              </Tooltip>
            </Space>
          </div>

          <div className="outline-tree-tools">
            <Input
              allowClear
              aria-label="搜索大纲"
              prefix={<SearchOutlined />}
              placeholder="搜索标题或摘要"
              value={searchKeyword}
              onChange={(event) => setSearchKeyword(event.target.value)}
            />
            <div className="outline-tree-filter-row">
              <Select
                aria-label="按节点类型筛选"
                value={typeFilter}
                onChange={setTypeFilter}
                options={[{ value: 'all', label: '全部类型' }, ...NODE_TYPE_OPTIONS]}
              />
              <Select
                aria-label="按节点状态筛选"
                value={statusFilter}
                onChange={setStatusFilter}
                options={[{ value: 'all', label: '全部状态' }, ...STATUS_OPTIONS]}
              />
              <Tooltip title="全部展开">
                <Button aria-label="全部展开" icon={<PlusSquareOutlined />} onClick={() => setExpandedKeys(collectTreeKeys(tree))} />
              </Tooltip>
              <Tooltip title="全部折叠">
                <Button aria-label="全部折叠" icon={<MinusSquareOutlined />} onClick={() => setExpandedKeys([])} />
              </Tooltip>
            </div>
          </div>

          {treeData.length === 0 && !loading ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={filterActive ? '没有符合条件的大纲节点' : '暂无大纲'}>
              {!filterActive && <Button type="primary" icon={<PlusOutlined />} onClick={() => startCreate(null)}>新增节点</Button>}
            </Empty>
          ) : (
            <Tree
              blockNode
              draggable
              treeData={treeData}
              selectedKeys={selectedId ? [selectedId] : []}
              expandedKeys={visibleExpandedKeys}
              onExpand={(keys) => setExpandedKeys(keys.map(String))}
              onSelect={(keys) => {
                confirmLeave(() => {
                  saveRequestGate.current.invalidate()
                  setSaving(false)
                  creatingRef.current = false
                  selectedIdRef.current = keys.length > 0 ? String(keys[0]) : null
                  setCreating(false)
                  setSelectedId(selectedIdRef.current)
                })
              }}
              onDrop={handleDrop}
            />
          )}
          <div
            className="outline-resize-handle"
            role="separator"
            aria-label="调整大纲导航宽度"
            aria-orientation="vertical"
            aria-valuemin={320}
            aria-valuemax={480}
            aria-valuenow={panelWidth}
            tabIndex={0}
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture(event.pointerId)
              resizeStartX.current = event.clientX
              resizeStartWidth.current = panelWidth
              setResizing(true)
            }}
            onPointerMove={(event) => {
              if (resizing) updatePanelWidth(resizeStartWidth.current + event.clientX - resizeStartX.current)
            }}
            onPointerUp={(event) => {
              if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
              setResizing(false)
            }}
            onPointerCancel={() => setResizing(false)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                event.preventDefault()
                updatePanelWidth(panelWidth + (event.key === 'ArrowRight' ? 16 : -16))
              }
            }}
          />
        </aside>

        <main className="outline-editor">
          <div className="outline-editor-head">
            <div>
              <Title level={4} style={{ margin: 0 }}>
                {editorTitle}
              </Title>
              {(creating || selectedNode || saveStatus === 'error') && (
                <Space size={8} wrap>
                  {!creating && selectedNode && (
                    <Text type="secondary">
                      {nodeTypeLabel(selectedNode.node_type)} · {statusLabel(selectedNode.status)}
                      {selectedCharacterNames ? ` · ${selectedCharacterNames}` : ''}
                    </Text>
                  )}
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

          {pendingOutlineDraft && (
            <div style={{ marginBottom: 16 }}>
              <OutlineDraftReviewPanel
                projectId={projectId}
                draft={pendingOutlineDraft}
                onFormalOutlineChanged={fetchOutline}
              />
            </div>
          )}

          {!creating && !selectedNode && tree.length === 0 ? (
            <Alert type="info" showIcon message="先创建一个大纲节点" />
          ) : (
            <Form
              form={form}
              layout="vertical"
              onFinish={saveOutlineNode}
              onValuesChange={() => {
                formRevisionRef.current += 1
                markDirty()
              }}
            >
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
                <Form.Item name="node_type" label="节点类型">
                  <Select options={NODE_TYPE_OPTIONS} />
                </Form.Item>
                <Form.Item name="status" label="状态">
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
