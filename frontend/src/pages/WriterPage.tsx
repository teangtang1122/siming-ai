import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Empty,
  Form,
  Input,
  List,
  Popconfirm,
  Select,
    Space,
    Tag,
    Timeline,
  Typography,
  message,
} from 'antd'
import {
  DeleteOutlined,
  DiffOutlined,
  FileTextOutlined,
  HistoryOutlined,
  PlusOutlined,
  ReloadOutlined,
  RollbackOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { useAiPanelContext } from '../contexts/AiPanelContext'
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




interface WriterPageProps {
  projectId: string
}

const STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  in_progress: 'processing',
  completed: 'success',
}

const TRIGGER_LABEL: Record<string, string> = {
  manual_save: '手动保存',
  ai_insert: 'AI 插入',
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

function WriterPage({ projectId }: WriterPageProps) {
  const [form] = Form.useForm<ChapterFormValues>()
  const [chapters, setChapters] = useState<ChapterItem[]>([])
  const [outlineOptions, setOutlineOptions] = useState<Array<{ value: string; label: string }>>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<ChapterDetail | null>(null)
  const [snapshots, setSnapshots] = useState<SnapshotItem[]>([])
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [diffLoading, setDiffLoading] = useState(false)
  const [fromSnapshotId, setFromSnapshotId] = useState<string | undefined>()
  const [toSnapshotId, setToSnapshotId] = useState<string | undefined>()
  const [diff, setDiff] = useState<DiffResponse | null>(null)

  const { setAiContext, refreshKey } = useAiPanelContext()

  const editorSelectionRef = useRef<{ start: number; end: number } | null>(null)
  const [selectedText, setSelectedText] = useState('')
  const [selectedTextChapterId, setSelectedTextChapterId] = useState<string | null>(null)


  const getSelectedText = (): string => {
    const el = document.querySelector<HTMLTextAreaElement>('.writer-content-input')
    if (!el) return ''
    const start = el.selectionStart ?? 0
    const end = el.selectionEnd ?? 0
    editorSelectionRef.current = { start, end }
    return el.value.substring(start, end)
  }

  const getContentTextArea = () => document.querySelector<HTMLTextAreaElement>('.writer-content-input')

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
      // ignore
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
      fetchSnapshots(chapterId)
    } catch (err: any) {
      message.error(err.message || '获取章节详情失败')
    }
  }, [fetchSnapshots, form, projectId])

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
      selectedOutlineNodeId: form.getFieldValue('outline_node_id') || null,
      selectedText,
      selectedTextChapterId,
    })
  }, [form.getFieldValue('outline_node_id'), selectedText, selectedTextChapterId, setAiContext])

  // Refresh data when AI applies changes
  useEffect(() => {
    if (refreshKey > 0) {
      fetchChapters()
      fetchOutline()
      if (selectedId) fetchDetail(selectedId)
    }
  }, [refreshKey])

  const startCreate = () => {
    setCreating(true)
    setSelectedId(null)
    setDetail(null)
    setSnapshots([])
    setDiff(null)
    form.setFieldsValue({ title: '', outline_node_id: undefined, content: '' })
  }

  const saveChapter = async (values: ChapterFormValues) => {
    if (!values.title.trim()) { message.warning('请输入章节标题'); return }
    setSaving(true)
    try {
      const payload = { title: values.title.trim(), outline_node_id: values.outline_node_id || null, content: values.content || '' }
      if (creating || !selectedId) {
        const res = await apiClient.post<ApiResponse<ChapterDetail>>(`/projects/${projectId}/chapters`, payload)
        setSelectedId(res.data.data.id)
        setCreating(false)
        message.success('章节已创建')
      } else {
        const res = await apiClient.put<ApiResponse<ChapterDetail>>(`/projects/${projectId}/chapters/${selectedId}`, { ...payload, trigger_type: 'manual_save' })
        setDetail(res.data.data)
        message.success('章节已保存并创建快照')
        fetchSnapshots(selectedId)
      }
      fetchChapters()
    } catch (err: any) {
      message.error(err.message || '保存章节失败')
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
      fetchChapters()
    } catch (err: any) {
      message.error(err.message || '删除章节失败')
    }
  }

  const restoreSnapshot = async (snapshotId: string) => {
    if (!selectedId) return
    try {
      const res = await apiClient.post<ApiResponse<ChapterDetail>>(`/projects/${projectId}/chapters/${selectedId}/restore/${snapshotId}`)
      setDetail(res.data.data)
      form.setFieldsValue({ title: res.data.data.title, outline_node_id: res.data.data.outline_node_id || undefined, content: res.data.data.content })
      message.success('已恢复历史版本')
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
              <Button icon={<ReloadOutlined />} onClick={fetchChapters} loading={loading} />
              <Button type="primary" icon={<PlusOutlined />} onClick={startCreate}>新建</Button>
            </Space>
          </div>
          <List
            loading={loading}
            dataSource={chapters}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无章节"><Button type="primary" icon={<PlusOutlined />} onClick={startCreate}>新建章节</Button></Empty> }}
            renderItem={(chapter) => (
              <List.Item
                className={`writer-chapter-item${chapter.id === selectedId ? ' writer-chapter-item-active' : ''}`}
                onClick={() => setSelectedId(chapter.id)}
              >
                <List.Item.Meta
                  title={<Text strong ellipsis={{ tooltip: chapter.title }} style={{ maxWidth: '100%' }}>{chapter.title}</Text>}
                  description={
                    <Space direction="vertical" size={4}>
                      <Text type="secondary" ellipsis>{chapter.outline_path.length > 0 ? chapter.outline_path.join(' / ') : '未关联大纲'}</Text>
                      <Space size={6} wrap>
                        <Tag>{chapter.word_count} 字</Tag>
                        <Tag>v{chapter.current_version}</Tag>
                        {chapter.outline_status && <Tag color={STATUS_COLOR[chapter.outline_status] || 'default'}>{chapter.outline_status}</Tag>}
                      </Space>
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        </aside>

        {/* ── Center: Editor ── */}
        <main className="writer-editor">
          <div className="writer-editor-head">
            <div>
              <Title level={4} style={{ margin: 0 }}>{editorTitle}</Title>
              {detail && !creating && (
                <Text type="secondary">{detail.word_count} 字 · v{detail.current_version} · {new Date(detail.updated_at).toLocaleString('zh-CN')}</Text>
              )}
            </div>
            <Space>
              {selectedId && !creating && (
                <>
                  <Popconfirm title="删除章节" description="版本历史和出场记录也会一并删除。" okText="删除" cancelText="取消"
                    okButtonProps={{ danger: true, autoInsertSpace: false }} cancelButtonProps={{ autoInsertSpace: false }}
                    onConfirm={deleteChapter}>
                    <Button danger icon={<DeleteOutlined />}>删除</Button>
                  </Popconfirm>
                </>
              )}
              <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => form.submit()}>保存</Button>
            </Space>
          </div>

          {!creating && !detail && chapters.length === 0 ? (
            <Alert type="info" showIcon message="先创建一个章节，正文和版本历史会从这里开始。" />
          ) : (
            <Form form={form} layout="vertical" onFinish={saveChapter}>
              <div className="writer-form-grid">
                <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入章节标题' }]}>
                  <Input placeholder="例如：第一章 风祭前夜" maxLength={200} />
                </Form.Item>
                <Form.Item name="outline_node_id" label="关联大纲">
                  <Select allowClear showSearch optionFilterProp="label" options={outlineOptions} placeholder="选择大纲节点" />
                </Form.Item>
              </div>
              {detail?.summary_text && !creating && (
                <Alert
                  type="info"
                  showIcon
                  message="章节摘要"
                  description={
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
                  }
                  style={{ marginBottom: 12 }}
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
          )}

          {/* ── Version History ── */}
          <section className="writer-history-section">
            <div className="writer-history-head">
              <Title level={5} style={{ margin: 0 }}><HistoryOutlined /> 版本历史</Title>
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

          </section>
        </main>

      </div>
    </div>
  )
}

export default WriterPage
