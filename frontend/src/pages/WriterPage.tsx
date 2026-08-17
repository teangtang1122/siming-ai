import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Collapse,
  Dropdown,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Popconfirm,
  Select,
  Space,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  AuditOutlined,
  DeleteOutlined,
  DiffOutlined,
  FileTextOutlined,
  HistoryOutlined,
  HighlightOutlined,
  MoreOutlined,
  PlusOutlined,
  ReloadOutlined,
  RollbackOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { SaveStatusIndicator } from '../components/interaction'
import { useAiPanelContext } from '../contexts/AiPanelContext'
import { useModelOptions } from '../hooks/useModelOptions'
import { useUnsavedGuard } from '../hooks/useUnsavedGuard'
import {
  WriterReviewDialogs,
  type DeAiPreview,
  type DeAiTarget,
  type QualityScorePreview,
  type QualityScoreTarget,
} from '../features/writer/WriterReviewDialogs'
import { useWriterSourceNavigation } from '../features/writer/useWriterSourceNavigation'
import './WriterPage.css'

const { Paragraph, Text, Title } = Typography
const { TextArea } = Input

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface ChapterItem {
  id: string
  project_id: string
  outline_node_id?: string | null
  title: string
  word_count: number
  current_version: number
  sort_order: number
  outline_title?: string | null
  outline_status?: string | null
  outline_node_type?: string | null
  outline_path: string[]
  summary_text?: string | null
  key_events?: string[]
  created_at: string
  updated_at: string
}

interface ChapterDetail extends ChapterItem {
  content: string
  snapshot_count: number
}

interface SnapshotItem {
  id: string
  chapter_id: string
  version_number: number
  word_count: number
  trigger_type: string
  created_at: string
}

interface OutlineNode {
  id: string
  parent_id?: string | null
  node_type: 'volume' | 'chapter' | 'section'
  title: string
  status: string
  sort_order: number
  children: OutlineNode[]
}

interface DiffChange {
  type: 'equal' | 'replace' | 'delete' | 'insert'
  from_start: number
  from_end: number
  to_start: number
  to_end: number
  from_lines: string[]
  to_lines: string[]
}

interface DiffResponse {
  from_snapshot: SnapshotItem
  to_snapshot: SnapshotItem
  changes: DiffChange[]
  total_changes: number
}

interface ChapterFormValues {
  title: string
  outline_node_id?: string
  content: string
}

interface AppliedDeAiRevision {
  before: string
  after: string
}

interface WriterPageProps {
  projectId: string
  focusChapterId?: string
  sourceLocatorKey?: string
}

const STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  in_progress: 'processing',
  completed: 'success',
}

const STATUS_LABEL: Record<string, string> = {
  pending: '待规划',
  in_progress: '进行中',
  completed: '已完成',
}

export function chapterStatusLabel(status?: string | null) {
  if (!status) return ''
  return STATUS_LABEL[status] || '未知状态'
}

const TRIGGER_LABEL: Record<string, string> = {
  manual_save: '手动保存',
  ai_insert: 'AI 插入',
  de_ai: '去除 AI 味',
  restore: '版本恢复',
}

function flattenOutline(nodes: OutlineNode[], depth = 0, prefix: string[] = []): Array<{
  id: string
  title: string
  depth: number
  path: string[]
}> {
  return nodes.flatMap((node) => {
    const path = [...prefix, node.title]
    return [
      { id: node.id, title: node.title, depth, path },
      ...flattenOutline(node.children || [], depth + 1, path),
    ]
  })
}

function WriterPage({ projectId, focusChapterId, sourceLocatorKey }: WriterPageProps) {
  const [form] = Form.useForm<ChapterFormValues>()
  const [chapters, setChapters] = useState<ChapterItem[]>([])
  const [draggedChapterId, setDraggedChapterId] = useState<string | null>(null)
  const [dragOverChapterId, setDragOverChapterId] = useState<string | null>(null)
  const [reordering, setReordering] = useState(false)
  const [outlineOptions, setOutlineOptions] = useState<Array<{ value: string; label: string }>>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const {
    isDirty,
    saveStatus,
    saveError,
    markDirty,
    markSaved,
    markSaving,
    markSaveFailed,
    confirmLeave,
  } = useUnsavedGuard()
  const [detail, setDetail] = useState<ChapterDetail | null>(null)
  const [snapshots, setSnapshots] = useState<SnapshotItem[]>([])
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [diffLoading, setDiffLoading] = useState(false)
  const [fromSnapshotId, setFromSnapshotId] = useState<string | undefined>()
  const [toSnapshotId, setToSnapshotId] = useState<string | undefined>()
  const [diff, setDiff] = useState<DiffResponse | null>(null)
  const [deAiOpen, setDeAiOpen] = useState(false)
  const [deAiLoading, setDeAiLoading] = useState(false)
  const [deAiModel, setDeAiModel] = useState<string>()
  const [deAiTarget, setDeAiTarget] = useState<DeAiTarget | null>(null)
  const [deAiPreview, setDeAiPreview] = useState<DeAiPreview | null>(null)
  const [appliedDeAiRevision, setAppliedDeAiRevision] = useState<AppliedDeAiRevision | null>(null)
  const [qualityOpen, setQualityOpen] = useState(false)
  const [qualityLoading, setQualityLoading] = useState(false)
  const [qualityModel, setQualityModel] = useState<string>()
  const [qualityTarget, setQualityTarget] = useState<QualityScoreTarget | null>(null)
  const [qualityPreview, setQualityPreview] = useState<QualityScorePreview | null>(null)

  const { setAiContext, refreshKey } = useAiPanelContext()
  const { modelOptions, defaultModel, loading: modelsLoading } = useModelOptions()

  const editorSelectionRef = useRef<{ start: number; end: number } | null>(null)
  const [selectedText, setSelectedText] = useState('')
  const [selectedTextChapterId, setSelectedTextChapterId] = useState<string | null>(null)
  const watchedOutlineNodeId = Form.useWatch('outline_node_id', form)
  const watchedContent = Form.useWatch('content', form)
  const chapterIds = useMemo(() => chapters.map((chapter) => chapter.id), [chapters])


  const getContentTextArea = () => document.querySelector<HTMLTextAreaElement>(
    'textarea.writer-content-input, .writer-content-input textarea',
  )

  const getSelectedText = (): string => {
    const el = getContentTextArea()
    if (!el) return ''
    const start = el.selectionStart ?? 0
    const end = el.selectionEnd ?? 0
    editorSelectionRef.current = { start, end }
    return el.value.substring(start, end)
  }

  const captureEditorSelection = () => {
    const el = getContentTextArea()
    if (!el) return
    editorSelectionRef.current = {
      start: el.selectionStart ?? 0,
      end: el.selectionEnd ?? 0,
    }
    const selected = getSelectedText()
    setSelectedText(selected)
    setSelectedTextChapterId(selectedId)
  }


  const fetchChapters = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<{ items: ChapterItem[]; total: number }>>(`/projects/${projectId}/chapters`)
      setChapters(res.data.data.items)
      if (!selectedId && !creating && res.data.data.items.length > 0) {
        setSelectedId(res.data.data.items[0].id)
      }
      if (selectedId && !res.data.data.items.some((item) => item.id === selectedId)) {
        setSelectedId(res.data.data.items[0]?.id || null)
      }
    } catch (err: any) {
      message.error(err.message || '获取章节列表失败')
    } finally {
      setLoading(false)
    }
  }, [creating, projectId, selectedId])

  const fetchOutline = useCallback(async () => {
    try {
      const res = await apiClient.get<ApiResponse<{ items: OutlineNode[]; flat: OutlineNode[]; total: number }>>(`/projects/${projectId}/outline`)
      const flattened = flattenOutline(res.data.data.items)
      setOutlineOptions(flattened.map((item) => ({ value: item.id, label: `${'　'.repeat(item.depth)}${item.path.join(' / ')}` })))
    } catch (err: any) {
      message.error(err.message || '获取大纲失败')
    }
  }, [projectId])

  const fetchSnapshots = useCallback(async (chapterId: string) => {
    try {
      const res = await apiClient.get<ApiResponse<{ items: SnapshotItem[]; total: number }>>(`/projects/${projectId}/chapters/${chapterId}/snapshots`)
      const items = res.data.data.items
      setSnapshots(items)
      setDiff(null)
      setFromSnapshotId(items[1]?.id || items[0]?.id)
      setToSnapshotId(items[0]?.id)
    } catch {
      message.warning('获取版本历史失败')
    }
  }, [projectId])

  const fetchDetail = useCallback(async (chapterId: string) => {
    try {
      const res = await apiClient.get<ApiResponse<ChapterDetail>>(`/projects/${projectId}/chapters/${chapterId}`)
      setDetail(res.data.data)
      setCreating(false)
      form.setFieldsValue({
        title: res.data.data.title,
        outline_node_id: res.data.data.outline_node_id || undefined,
        content: res.data.data.content,
      })
      setAppliedDeAiRevision(null)
      markSaved()
      fetchSnapshots(chapterId)
    } catch (err: any) {
      message.error(err.message || '获取章节详情失败')
    }
  }, [fetchSnapshots, form, markSaved, projectId])

  const focusSourceChapter = useCallback((chapterId: string) => {
    setCreating(false)
    setSelectedId(chapterId)
  }, [])

  const selectSourceEvidence = useCallback((
    range: { start: number; end: number },
    text: string,
    chapterId: string,
  ) => {
    editorSelectionRef.current = range
    setSelectedText(text)
    setSelectedTextChapterId(chapterId)
  }, [])

  useWriterSourceNavigation({
    projectId,
    focusChapterId,
    sourceLocatorKey,
    chapterIds,
    detail,
    confirmLeave,
    onFocusChapter: focusSourceChapter,
    onEvidenceSelected: selectSourceEvidence,
  })

  useEffect(() => {
    fetchOutline()
    fetchChapters()
  }, [fetchChapters, fetchOutline])

  useEffect(() => {
    if (selectedId) {
      fetchDetail(selectedId)

    } else if (!creating) {
      setDetail(null)
      setSnapshots([])

      form.resetFields()
    }
  }, [creating, fetchDetail, form, selectedId])

  // Sync selections to AI context
  useEffect(() => {
    setAiContext({
      selectedOutlineNodeId: watchedOutlineNodeId || null,
      selectedText,
      selectedTextChapterId,
    })
  }, [selectedText, selectedTextChapterId, setAiContext, watchedOutlineNodeId])

  // Refresh data when AI applies changes
  useEffect(() => {
    if (refreshKey > 0) {
      fetchChapters()
      fetchOutline()
      if (selectedId) fetchDetail(selectedId)
    }
  }, [fetchChapters, fetchDetail, fetchOutline, refreshKey, selectedId])

  const startCreate = () => {
    confirmLeave(() => {
      setCreating(true)
      setSelectedId(null)
      setDetail(null)
      setSnapshots([])
      setDiff(null)
      setAppliedDeAiRevision(null)
      form.setFieldsValue({ title: '', outline_node_id: undefined, content: '' })
      markSaved()
    })
  }

  const confirmDeleteChapter = () => {
    const chapterTitle = detail?.title
      || chapters.find((chapter) => chapter.id === selectedId)?.title
      || '当前章节'
    Modal.confirm({
      title: `删除「${chapterTitle}」？`,
      content: '该章节的正文、版本历史和出场记录都会一并删除，此操作不可恢复。',
      okText: '删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: deleteChapter,
    })
  }

  const saveChapter = async (values: ChapterFormValues) => {
    if (!values.title.trim()) { message.warning('请输入章节标题'); return }
    setSaving(true)
    markSaving()
    try {
      const payload = { title: values.title.trim(), outline_node_id: values.outline_node_id || null, content: values.content || '' }
      if (creating || !selectedId) {
        const res = await apiClient.post<ApiResponse<ChapterDetail>>(`/projects/${projectId}/chapters`, payload)
        setSelectedId(res.data.data.id)
        setCreating(false)
      } else {
        const res = await apiClient.put<ApiResponse<ChapterDetail>>(`/projects/${projectId}/chapters/${selectedId}`, {
          ...payload,
          trigger_type: appliedDeAiRevision ? 'de_ai' : 'manual_save',
        })
        setDetail(res.data.data)
        fetchSnapshots(selectedId)
      }
      setAppliedDeAiRevision(null)
      markSaved()
      fetchChapters()
    } catch (err: any) {
      markSaveFailed(err.message || '保存章节失败')
    } finally {
      setSaving(false)
    }
  }

  const deleteChapter = async () => {
    if (!selectedId) return
    try {
      await apiClient.delete(`/projects/${projectId}/chapters/${selectedId}`)
      message.success('章节已删除')
      setSelectedId(null)
      setDetail(null)
      setAppliedDeAiRevision(null)
      fetchChapters()
    } catch (err: any) {
      message.error(err.message || '删除章节失败')
    }
  }

  const saveChapterOrder = async (nextChapters: ChapterItem[]) => {
    if (reordering) return
    const previous = chapters
    const optimistic = nextChapters.map((chapter, index) => ({
      ...chapter,
      sort_order: (index + 1) * 1000,
    }))
    setChapters(optimistic)
    setReordering(true)
    try {
      const res = await apiClient.put<ApiResponse<{ items: ChapterItem[]; total: number }>>(
        `/projects/${projectId}/chapters/reorder`,
        { ids: optimistic.map((chapter) => chapter.id) },
      )
      setChapters(res.data.data.items)
      message.success('正文顺序已更新')
    } catch (err: any) {
      setChapters(previous)
      message.error(err.message || '调整正文顺序失败')
    } finally {
      setReordering(false)
    }
  }

  const moveChapterByOffset = (chapterId: string, offset: -1 | 1) => {
    const index = chapters.findIndex((chapter) => chapter.id === chapterId)
    const target = index + offset
    if (index < 0 || target < 0 || target >= chapters.length || reordering) return
    const next = [...chapters]
    ;[next[index], next[target]] = [next[target], next[index]]
    void saveChapterOrder(next)
  }

  const placeChapterBefore = (sourceId: string, targetId: string) => {
    if (sourceId === targetId || reordering) return
    const next = [...chapters]
    const sourceIndex = next.findIndex((chapter) => chapter.id === sourceId)
    if (sourceIndex < 0) return
    const [moved] = next.splice(sourceIndex, 1)
    const targetIndex = next.findIndex((chapter) => chapter.id === targetId)
    if (targetIndex < 0) return
    next.splice(targetIndex, 0, moved)
    void saveChapterOrder(next)
  }

  const restoreSnapshot = async (snapshotId: string) => {
    if (!selectedId) return
    try {
      const res = await apiClient.post<ApiResponse<ChapterDetail>>(`/projects/${projectId}/chapters/${selectedId}/restore/${snapshotId}`)
      setDetail(res.data.data)
      form.setFieldsValue({ title: res.data.data.title, outline_node_id: res.data.data.outline_node_id || undefined, content: res.data.data.content })
      setAppliedDeAiRevision(null)
      message.success('已恢复历史版本')
      markSaved()
      fetchSnapshots(selectedId)
      fetchChapters()
    } catch (err: any) {
      message.error(err.message || '恢复版本失败')
    }
  }

  const compareSnapshots = async () => {
    if (!selectedId || !fromSnapshotId || !toSnapshotId) return
    if (fromSnapshotId === toSnapshotId) { message.warning('请选择两个不同版本'); return }
    setDiffLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<DiffResponse>>(`/projects/${projectId}/chapters/${selectedId}/snapshots/diff`, { from_snapshot_id: fromSnapshotId, to_snapshot_id: toSnapshotId })
      setDiff(res.data.data)
    } catch (err: any) {
      message.error(err.message || '版本对比失败')
    } finally {
      setDiffLoading(false)
    }
  }

  const resetDeAiDialog = () => {
    setDeAiOpen(false)
    setDeAiPreview(null)
    setDeAiTarget(null)
    setDeAiLoading(false)
  }

  const resetQualityDialog = () => {
    setQualityOpen(false)
    setQualityPreview(null)
    setQualityTarget(null)
    setQualityLoading(false)
  }

  const openQualityDialog = () => {
    if (!selectedId || creating) return
    const content = String(form.getFieldValue('content') || '')
    if (content.trim().length < 20) {
      message.warning('正文太短，请至少填写 20 个字符后再评分')
      return
    }
    if (modelOptions.length === 0) {
      message.warning('请先在模型与训练中启用一个可用模型')
      return
    }
    setQualityTarget({
      title: String(form.getFieldValue('title') || detail?.title || '未命名章节'),
      content,
    })
    setQualityModel(defaultModel || modelOptions[0]?.value)
    setQualityPreview(null)
    setQualityOpen(true)
  }

  const generateQualityScore = async () => {
    if (!selectedId || !qualityTarget) return
    if (!qualityModel) {
      message.warning('请选择评分模型')
      return
    }
    setQualityLoading(true)
    try {
      const res = await apiClient.post<ApiResponse<QualityScorePreview>>(
        `/projects/${projectId}/chapters/${selectedId}/quality-score-preview`,
        { ...qualityTarget, model: qualityModel },
      )
      setQualityPreview(res.data.data)
    } catch (err: any) {
      message.error(err.message || '质量评分失败，正文未发生变化')
    } finally {
      setQualityLoading(false)
    }
  }

  const openDeAiDialog = () => {
    if (!selectedId || creating) return
    const baseContent = String(form.getFieldValue('content') || '')
    if (!baseContent.trim()) {
      message.warning('请先填写章节正文')
      return
    }
    if (modelOptions.length === 0) {
      message.warning('请先在模型与训练中启用一个可用模型')
      return
    }

    const editor = getContentTextArea()
    const start = editor?.selectionStart ?? 0
    const end = editor?.selectionEnd ?? 0
    const selected = end > start ? baseContent.slice(start, end) : ''
    if (selected.trim() && selected.trim().length < 20) {
      message.warning('选中文字太短，请至少选择 20 个字符，或取消选区后处理整章')
      return
    }
    const useSelection = Boolean(selected.trim())
    setDeAiTarget({
      scope: useSelection ? 'selection' : 'chapter',
      baseContent,
      source: useSelection ? selected : baseContent,
      start: useSelection ? start : 0,
      end: useSelection ? end : baseContent.length,
    })
    setDeAiModel(defaultModel || modelOptions[0]?.value)
    setDeAiPreview(null)
    setDeAiOpen(true)
  }

  const generateDeAiPreview = async () => {
    if (!selectedId || !deAiTarget) return
    if (!deAiModel) {
      message.warning('请选择执行模型')
      return
    }
    setDeAiLoading(true)
    try {
      const previousRound = deAiPreview?.revision_round || (deAiPreview ? 1 : 0)
      const nextRound = Math.min(previousRound + 1, 3)
      const requestPayload = deAiPreview
        ? {
            content: deAiPreview.rewritten,
            original_content: deAiTarget.source,
            revision_round: nextRound,
            model: deAiModel,
          }
        : { content: deAiTarget.source, model: deAiModel }
      const res = await apiClient.post<ApiResponse<DeAiPreview>>(
        `/projects/${projectId}/chapters/${selectedId}/de-ai-preview`,
        requestPayload,
      )
      setDeAiPreview(res.data.data)
      if (res.data.data.audit_passed === false) {
        message.warning('候选稿已生成，但有系统审核提醒；原文未变，请对照后自行决定是否替换')
      }
    } catch (err: any) {
      message.error(err.message || '去除 AI 味失败，原文未发生变化')
    } finally {
      setDeAiLoading(false)
    }
  }

  const commitDeAiPreviewApplication = () => {
    if (!deAiTarget || !deAiPreview) return
    const nextContent = `${deAiTarget.baseContent.slice(0, deAiTarget.start)}${deAiPreview.rewritten}${deAiTarget.baseContent.slice(deAiTarget.end)}`
    form.setFieldValue('content', nextContent)
    setAppliedDeAiRevision({ before: deAiTarget.baseContent, after: nextContent })
    markDirty()
    setSelectedText('')
    setSelectedTextChapterId(null)
    editorSelectionRef.current = null
    resetDeAiDialog()
    message.success('候选稿已应用到编辑器；确认无误后再保存')
  }

  const applyDeAiPreview = () => {
    if (!deAiTarget || !deAiPreview) return
    if (deAiPreview.audit_passed !== false) {
      commitDeAiPreviewApplication()
      return
    }
    Modal.confirm({
      title: '候选稿有审核提醒，仍要替换吗？',
      content: '系统没有自动采用这份稿件，原文目前仍保持不变。请确认已对照阅读候选稿和审核提醒。',
      okText: '仍要替换到编辑器',
      cancelText: '继续对照',
      onOk: commitDeAiPreviewApplication,
    })
  }

  const undoAppliedDeAiRevision = () => {
    if (!appliedDeAiRevision) return
    const restore = () => {
      form.setFieldValue('content', appliedDeAiRevision.before)
      setAppliedDeAiRevision(null)
      markDirty()
      message.success('已撤销本次去除 AI 味')
    }
    if (String(form.getFieldValue('content') || '') === appliedDeAiRevision.after) {
      restore()
      return
    }
    Modal.confirm({
      title: '撤销去除 AI 味并放弃后续编辑？',
      content: '应用候选稿后正文又有改动。继续撤销会一并放弃这些改动。',
      okText: '仍然撤销',
      cancelText: '保留正文',
      onOk: restore,
    })
  }

  const snapshotOptions = useMemo(() => snapshots.map((snapshot) => ({
    value: snapshot.id,
    label: `v${snapshot.version_number} · ${TRIGGER_LABEL[snapshot.trigger_type] || snapshot.trigger_type}`,
  })), [snapshots])

  const editorTitle = creating ? '新建章节' : detail?.title || '章节正文'

  return (
    <div className="writer-page">
      <div className="writer-shell">
        {/* ── Left: Chapter List ── */}
        <aside className="writer-chapter-panel">
          <div className="writer-panel-head">
            <Title level={4} style={{ margin: 0 }}><FileTextOutlined /> 章节</Title>
            <Space size={6}>
              <Button aria-label="刷新章节列表" icon={<ReloadOutlined />} onClick={fetchChapters} loading={loading} />
              <Button type="primary" icon={<PlusOutlined />} aria-label="新建章节" onClick={startCreate}>新建</Button>
            </Space>
          </div>
          <List
            loading={loading}
            dataSource={chapters}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无章节"><Button type="primary" icon={<PlusOutlined />} onClick={startCreate}>新建章节</Button></Empty> }}
            renderItem={(chapter, index) => (
              <List.Item
                className={`writer-chapter-item${chapter.id === selectedId ? ' writer-chapter-item-active' : ''}${chapter.id === dragOverChapterId ? ' writer-chapter-item-drag-over' : ''}`}
                role="button"
                tabIndex={0}
                aria-label={`打开章节：${chapter.title}`}
                title="拖动章节卡片，或使用上下按钮调整正文顺序"
                draggable={!loading && !reordering}
                onDragStart={(event) => {
                  setDraggedChapterId(chapter.id)
                  event.dataTransfer.effectAllowed = 'move'
                  event.dataTransfer.setData('text/plain', chapter.id)
                }}
                onDragOver={(event) => {
                  if (!draggedChapterId || draggedChapterId === chapter.id) return
                  event.preventDefault()
                  event.dataTransfer.dropEffect = 'move'
                  setDragOverChapterId(chapter.id)
                }}
                onDrop={(event) => {
                  event.preventDefault()
                  const sourceId = draggedChapterId || event.dataTransfer.getData('text/plain')
                  setDraggedChapterId(null)
                  setDragOverChapterId(null)
                  if (sourceId) placeChapterBefore(sourceId, chapter.id)
                }}
                onDragEnd={() => {
                  setDraggedChapterId(null)
                  setDragOverChapterId(null)
                }}
                onClick={() => confirmLeave(() => setSelectedId(chapter.id))}
                onKeyDown={(event) => {
                  if (event.key !== 'Enter' && event.key !== ' ') return
                  event.preventDefault()
                  confirmLeave(() => setSelectedId(chapter.id))
                }}
              >
                <div className="writer-chapter-order-controls" aria-label={`调整章节顺序：${chapter.title}`}>
                  <Tooltip title="上移">
                    <Button
                      type="text"
                      size="small"
                      icon={<ArrowUpOutlined />}
                      aria-label={`上移章节：${chapter.title}`}
                      disabled={index === 0 || reordering}
                      onClick={(event) => {
                        event.stopPropagation()
                        moveChapterByOffset(chapter.id, -1)
                      }}
                    />
                  </Tooltip>
                  <Tooltip title="下移">
                    <Button
                      type="text"
                      size="small"
                      icon={<ArrowDownOutlined />}
                      aria-label={`下移章节：${chapter.title}`}
                      disabled={index === chapters.length - 1 || reordering}
                      onClick={(event) => {
                        event.stopPropagation()
                        moveChapterByOffset(chapter.id, 1)
                      }}
                    />
                  </Tooltip>
                </div>
                <List.Item.Meta
                  title={<span className="writer-chapter-title" title={chapter.title}>{chapter.title}</span>}
                  description={
                    <div className="writer-chapter-meta">
                      <Text type="secondary" ellipsis title={chapter.outline_path.join(' / ')}>{chapter.outline_path.length > 0 ? chapter.outline_path.join(' / ') : '未关联大纲'}</Text>
                      <div className="writer-chapter-facts">
                        <span>{chapter.word_count} 字</span>
                        <span>v{chapter.current_version}</span>
                        {chapter.outline_status && <Tag color={STATUS_COLOR[chapter.outline_status] || 'default'}>{chapterStatusLabel(chapter.outline_status)}</Tag>}
                      </div>
                    </div>
                  }
                />
              </List.Item>
            )}
          />
        </aside>

        {/* ── Center: Editor ── */}
        <main className="writer-editor">
          <div className="writer-editor-head">
            <div className="writer-editor-heading">
              <Title level={4} className="writer-editor-title" title={editorTitle}>{editorTitle}</Title>
              {detail && !creating && (
                <Space size={8} wrap>
                  <Text type="secondary">{detail.word_count} 字 · v{detail.current_version} · {new Date(detail.updated_at).toLocaleString('zh-CN')}</Text>
                  <SaveStatusIndicator status={saveStatus} error={saveError} />
                </Space>
              )}
            </div>
            <Space>
              {selectedId && !creating && (
                <Tooltip title={modelOptions.length === 0 ? '请先启用一个可用模型' : '按编辑器中的当前整章评分；只读，不修改正文'}>
                  <Button
                    icon={<AuditOutlined />}
                    disabled={String(watchedContent || '').trim().length < 20 || modelOptions.length === 0}
                    loading={qualityLoading}
                    onClick={openQualityDialog}
                  >
                    质量评分
                  </Button>
                </Tooltip>
              )}
              {selectedId && !creating && (
                <Tooltip title={modelOptions.length === 0 ? '请先启用一个可用模型' : '有选中文本时处理选段，否则处理整章'}>
                  <Button
                    icon={<HighlightOutlined />}
                    disabled={!String(watchedContent || '').trim() || modelOptions.length === 0}
                    loading={deAiLoading}
                    onClick={openDeAiDialog}
                  >
                    去除 AI 味
                  </Button>
                </Tooltip>
              )}
              {selectedId && !creating && (
                <Dropdown
                  trigger={['click']}
                  placement="bottomRight"
                  menu={{
                    items: [{ key: 'delete', danger: true, icon: <DeleteOutlined />, label: '删除本章' }],
                    onClick: ({ key }) => {
                      if (key === 'delete') confirmDeleteChapter()
                    },
                  }}
                >
                  <Button icon={<MoreOutlined />} aria-label={`章节操作：${detail?.title || editorTitle}`}>
                    章节操作
                  </Button>
                </Dropdown>
              )}
              <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={!creating && !isDirty} onClick={() => form.submit()}>
                {creating ? '创建章节' : '保存改动'}
              </Button>
            </Space>
          </div>

          {!creating && !detail && chapters.length === 0 ? (
            <Alert type="info" showIcon message="先创建一个章节，正文和版本历史会从这里开始。" />
          ) : (
            <>
              {appliedDeAiRevision && (
                <Alert
                  className="writer-de-ai-applied"
                  type="info"
                  showIcon
                  message="去除 AI 味候选稿已应用，尚未保存"
                  description="请通读确认；保存后会生成一个可恢复的版本快照。"
                  action={<Button size="small" onClick={undoAppliedDeAiRevision}>撤销应用</Button>}
                />
              )}
              <Form form={form} layout="vertical" onFinish={saveChapter} onValuesChange={markDirty}>
                <div className="writer-form-grid">
                  <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入章节标题' }]}>
                    <Input placeholder="例如：第一章 风祭前夜" maxLength={200} />
                  </Form.Item>
                  <Form.Item name="outline_node_id" label="关联大纲">
                    <Select allowClear showSearch optionFilterProp="label" options={outlineOptions} placeholder="选择大纲节点" />
                  </Form.Item>
                </div>
                {detail?.summary_text && !creating && (
                  <Collapse
                  className="writer-summary"
                  size="small"
                  items={[{
                    key: 'summary',
                    label: <Space size={8}><Text strong>章节摘要</Text><Text type="secondary">{(detail.key_events || []).length} 个关键事件</Text></Space>,
                    children: (
                      <div>
                        <Paragraph style={{ marginBottom: detail.key_events?.length ? 8 : 0, whiteSpace: 'pre-wrap' }}>
                          {detail.summary_text}
                        </Paragraph>
                        {(detail.key_events || []).length > 0 && (
                          <Space wrap>
                            {(detail.key_events || []).slice(0, 8).map((event, index) => (
                              <Tag key={`${event}-${index}`}>{event}</Tag>
                            ))}
                          </Space>
                        )}
                      </div>
                    ),
                  }]}
                  />
                )}
                <Form.Item name="content" label="正文">
                  <TextArea
                  className="writer-content-input"
                  placeholder="开始写这一章"
                  autoSize={{ minRows: 18, maxRows: 28 }}
                  showCount
                  onSelect={captureEditorSelection}
                  onMouseUp={captureEditorSelection}
                  onBlur={captureEditorSelection}
                  onKeyUp={captureEditorSelection}
                  />
                </Form.Item>
              </Form>
            </>
          )}

          {/* ── Version History ── */}
          <section className="writer-history-section" aria-label="版本历史">
            <Collapse
              items={[{
                key: 'versions',
                label: <Space><HistoryOutlined /><Text strong>版本历史</Text><Tag>{snapshots.length}</Tag></Space>,
                children: (
                  <>
                    <div className="writer-history-head">
                      <Text type="secondary">对比历史快照，或在不满意时恢复到旧版本。</Text>
                      <Space wrap>
                        <Select value={fromSnapshotId} options={snapshotOptions} onChange={setFromSnapshotId} placeholder="起始版本" style={{ width: 180 }} />
                        <Select value={toSnapshotId} options={snapshotOptions} onChange={setToSnapshotId} placeholder="目标版本" style={{ width: 180 }} />
                        <Button icon={<DiffOutlined />} loading={diffLoading} disabled={snapshots.length < 2} onClick={compareSnapshots}>对比</Button>
                      </Space>
                    </div>
                    {snapshots.length === 0 ? (
                      <Text type="secondary">保存一次正文后，这里会出现版本快照。</Text>
                    ) : (
                      <Timeline className="writer-snapshot-timeline" items={snapshots.map((snapshot) => ({
                        children: (
                          <div className="writer-snapshot-row">
                            <div><Text strong>v{snapshot.version_number}</Text>
                              <Text type="secondary"> · {TRIGGER_LABEL[snapshot.trigger_type] || snapshot.trigger_type} · {snapshot.word_count} 字 · {new Date(snapshot.created_at).toLocaleString('zh-CN')}</Text></div>
                            <Popconfirm title="恢复此版本" description="当前正文会被替换，并生成一条新的恢复快照。" okText="恢复" cancelText="取消" onConfirm={() => restoreSnapshot(snapshot.id)}>
                              <Button size="small" icon={<RollbackOutlined />}>恢复</Button>
                            </Popconfirm>
                          </div>
                        ),
                      }))} />
                    )}
                    {diff && (
                      <div className="writer-diff-panel">
                        <div className="writer-diff-summary">
                          <Text strong>v{diff.from_snapshot.version_number} → v{diff.to_snapshot.version_number}</Text>
                          <Tag color={diff.total_changes > 0 ? 'orange' : 'green'}>{diff.total_changes} 处变更</Tag>
                        </div>
                        {diff.changes.filter((change) => change.type !== 'equal').map((change, index) => (
                          <div className="writer-diff-change" key={`${change.type}-${index}`}>
                            <Tag color={change.type === 'insert' ? 'green' : change.type === 'delete' ? 'red' : 'orange'}>{change.type}</Tag>
                            <div className="writer-diff-columns">
                              <pre className="writer-diff-block writer-diff-old">{change.from_lines.length > 0 ? change.from_lines.join('\n') : ' '}</pre>
                              <pre className="writer-diff-block writer-diff-new">{change.to_lines.length > 0 ? change.to_lines.join('\n') : ' '}</pre>
                            </div>
                          </div>
                        ))}
                        {diff.total_changes === 0 && <Paragraph>两个版本没有正文差异。</Paragraph>}
                      </div>
                    )}
                  </>
                ),
              }]}
            />
          </section>
        </main>

      </div>

      <WriterReviewDialogs
        modelOptions={modelOptions}
        modelsLoading={modelsLoading}
        quality={{
          open: qualityOpen,
          loading: qualityLoading,
          model: qualityModel,
          target: qualityTarget,
          preview: qualityPreview,
          onModelChange: setQualityModel,
          onClose: resetQualityDialog,
          onGenerate: generateQualityScore,
        }}
        deAi={{
          open: deAiOpen,
          loading: deAiLoading,
          model: deAiModel,
          target: deAiTarget,
          preview: deAiPreview,
          onModelChange: setDeAiModel,
          onClose: resetDeAiDialog,
          onGenerate: generateDeAiPreview,
          onApply: applyDeAiPreview,
        }}
      />
    </div>
  )
}

export default WriterPage
