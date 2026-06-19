import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Divider,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Progress,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import type { UploadFile } from 'antd'
import {
  BookOutlined,
  DeleteOutlined,
  EditOutlined,
  FileAddOutlined,
  PlusOutlined,
  RobotOutlined,
  SaveOutlined,
  SearchOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import SystemNav from '../components/SystemNav'
import PageWrapper from '../components/PageWrapper'
import { apiClient } from '../api/client'
import { useAppStore } from '../stores'
import './DashboardPage.css'

const { Paragraph, Text, Title } = Typography
const { TextArea } = Input

interface ProjectFormValues {
  title: string
  description?: string
  tags?: string
}

interface NovelBriefValues {
  genre?: string
  target_audience?: string
  platform?: string
  user_brief: string
  reference_examples?: string
  template_id?: string
}

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface UploadResult {
  filename: string
  format: string
  text: string
  word_count: number
}

interface SplitItem {
  title: string
  start_char: number
  end_char: number
  preview: string
  needs_review?: boolean
  review_reason?: string
  source?: string
  block_index?: number
}

interface SplitResult {
  splits: SplitItem[]
  total: number
  method: string
  needs_review: boolean
  failed_blocks: number
}

interface ConfirmResult {
  chapters: Array<{ id: string; title: string; word_count: number }>
  total: number
}

interface NovelStartData {
  session_id: string
  checklist: unknown
  prompt_pack?: unknown
  missing_fields?: string[]
}

interface NovelBlueprint {
  title: string
  subtitle?: string
  logline?: string
  premise?: string
  genre?: string
  genre_positioning?: string
  core_conflict?: string
  world_hook?: string
  opening_scene?: string
  estimated_chapters?: number
  selling_points?: string[]
  protagonist?: {
    name?: string
    goal?: string
    conflict?: string
    personality?: string
    background?: string
  }
  characters?: Array<{ name?: string; role_type?: string; background?: string }>
  relationships?: Array<{ relationship_type?: string }>
  worldbuilding?: Array<{ title?: string; dimension?: string; content?: string }>
  volume_outline?: Array<{ title?: string; summary?: string }>
  outline?: Array<{ title?: string; summary?: string }>
  golden_three?: {
    opening_scene?: string
    chapter_1?: string
    chapter_2?: string
    chapter_3?: string
    promise?: string
  }
  creative_slots?: Record<string, string | string[]>
  requirement_coverage?: {
    score?: number
    covered?: string[]
    missing?: string[]
    warnings?: string[]
  }
  quality_self_check?: {
    score?: number
    pass?: boolean
    issues?: string[]
    suggestions?: string[]
  }
  creation_engine?: string
  deep_optimization_available?: boolean
}

interface NovelDraftData {
  session_id: string
  blueprints: NovelBlueprint[]
  recommendation?: string
  revision_mode?: string
  feedback?: string
  enhancement_mode?: string
  compiled_brief?: Record<string, unknown>
  coverage_summary?: Array<{ title?: string; score?: number; missing?: string[] }>
}

interface NovelApplyData {
  project_id: string
  characters: string[]
  worldbuilding: string[]
  outline: string[]
  relationships?: string[]
}

interface AssistantMessage {
  role: 'user' | 'assistant'
  content: string
}

interface CreationTemplate {
  id: string
  name: string
  brief: string
  creative_slots?: Record<string, string | string[]>
}

const CREATION_TEMPLATE_KEY = 'moshu:novelCreationTemplates'

const GENRE_OPTIONS = [
  { label: '仙侠', value: 'xianxia' },
  { label: '玄幻', value: 'fantasy' },
  { label: '都市', value: 'urban' },
  { label: '科幻', value: 'scifi' },
  { label: '悬疑', value: 'mystery' },
  { label: '言情', value: 'romance' },
  { label: '历史', value: 'history' },
  { label: '其他', value: 'other' },
]

const AUDIENCE_OPTIONS = [
  { label: '男频读者', value: 'male' },
  { label: '女频读者', value: 'female' },
  { label: '青少年', value: 'young' },
  { label: '全年龄', value: 'all' },
]

const PLATFORM_OPTIONS = [
  { label: '起点', value: 'qidian' },
  { label: '番茄', value: 'tomato' },
  { label: '晋江', value: 'jjwxc' },
  { label: '知乎', value: 'zhihu' },
  { label: '自出版', value: 'self_publish' },
]

function parseTags(value?: string) {
  return (value || '')
    .split(/[,，、\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function tagsPayload(value?: string) {
  const tags = parseTags(value)
  return tags.length ? tags : undefined
}

function tagsToFormValue(tagsStr?: string) {
  if (!tagsStr) return ''
  try {
    const tags = JSON.parse(tagsStr)
    return Array.isArray(tags) ? tags.join('，') : ''
  } catch {
    return ''
  }
}

function titleFromFile(file: File) {
  return file.name.replace(/\.(txt|docx)$/i, '').trim()
}

function buildCreationBrief(values: NovelBriefValues) {
  const base = (values.user_brief || '').trim()
  const references = (values.reference_examples || '').trim()
  return references ? `${base}\n\n参考样例/风格：${references}` : base
}

function slotValueToText(value?: string | string[]) {
  if (Array.isArray(value)) return value.join('、')
  return value || ''
}

function slotDraftToFeedback(slots: Record<string, string | string[]>) {
  const labels: Record<string, string> = {
    story_engine: '故事发动机',
    genre_fusion: '类型融合',
    protagonist_design: '主角设计',
    world_rules: '世界规则',
    conflict_engine: '冲突发动机',
    reader_promise: '读者承诺',
    scale_plan: '篇幅规划',
    custom_motifs: '创意要素',
    avoid_list: '禁用/避免',
    reference_examples: '参考样例',
  }
  return Object.entries(slots)
    .map(([key, value]) => `${labels[key] || key}：${slotValueToText(value)}`)
    .filter((line) => line.trim().length > 2)
    .join('\n')
}

function readCreationTemplates(): CreationTemplate[] {
  try {
    const raw = localStorage.getItem(CREATION_TEMPLATE_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function DashboardPage() {
  const navigate = useNavigate()
  const {
    projects,
    loading,
    fetchProjects,
    createProject,
    updateProject,
    deleteProject,
  } = useAppStore()

  const [searchKeyword, setSearchKeyword] = useState('')
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [importStatus, setImportStatus] = useState('')
  const [pendingImportFile, setPendingImportFile] = useState<File | null>(null)
  const [assistantBusy, setAssistantBusy] = useState(false)
  const [assistantSessionId, setAssistantSessionId] = useState('')
  const [assistantRecommendation, setAssistantRecommendation] = useState('')
  const [assistantDraftText, setAssistantDraftText] = useState('')
  const [assistantMessages, setAssistantMessages] = useState<AssistantMessage[]>([])
  const [blueprints, setBlueprints] = useState<NovelBlueprint[]>([])
  const [applyingBlueprintIndex, setApplyingBlueprintIndex] = useState<number | null>(null)
  const [creationTemplates, setCreationTemplates] = useState<CreationTemplate[]>([])
  const [slotEditorOpen, setSlotEditorOpen] = useState(false)
  const [slotBlueprintIndex, setSlotBlueprintIndex] = useState<number | null>(null)
  const [slotDraft, setSlotDraft] = useState<Record<string, string | string[]>>({})
  const [editingProject, setEditingProject] = useState<{
    id: string
    title: string
    description?: string
    tags?: string
  } | null>(null)
  const [form] = Form.useForm<ProjectFormValues>()
  const [editForm] = Form.useForm<ProjectFormValues>()
  const [assistantForm] = Form.useForm<NovelBriefValues>()

  useEffect(() => {
    fetchProjects()
  }, [fetchProjects])

  useEffect(() => {
    setCreationTemplates(readCreationTemplates())
  }, [])

  const pendingUploadList = useMemo<UploadFile[]>(() => {
    if (!pendingImportFile) return []
    return [{
      uid: 'pending-import-file',
      name: pendingImportFile.name,
      status: 'done',
    }]
  }, [pendingImportFile])

  const handleSearch = (value: string) => {
    setSearchKeyword(value)
    fetchProjects(value)
  }

  const closeCreateModal = () => {
    if (creating) return
    setIsCreateModalOpen(false)
    setPendingImportFile(null)
    setImportStatus('')
    form.resetFields()
  }

  const openCreateModal = (draft?: Partial<ProjectFormValues>, file?: File) => {
    setIsCreateModalOpen(true)
    setImportStatus('')
    if (file) setPendingImportFile(file)
    if (draft) form.setFieldsValue(draft)
  }

  const openAssistant = () => {
    setAssistantOpen(true)
    if (!assistantMessages.length) {
      setAssistantMessages([{
        role: 'assistant',
        content: '先告诉我题材、主角、核心卖点或你想避开的写法。我会先给出三套可比较的新书方案；不满意可以继续说，我可以在当前基础上调整，也可以全部重新生成。',
      }])
    }
  }

  const handleTemplateChange = (templateId?: string) => {
    if (!templateId) return
    const template = creationTemplates.find((item) => item.id === templateId)
    if (!template) return
    const currentBrief = assistantForm.getFieldValue('user_brief') || ''
    assistantForm.setFieldsValue({
      user_brief: currentBrief ? `${currentBrief}\n\n套用模板：${template.brief}` : template.brief,
    })
    message.success('已套用创作模板')
  }

  const saveBlueprintAsTemplate = (blueprint: NovelBlueprint) => {
    const nextTemplate: CreationTemplate = {
      id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      name: blueprint.title || `创作模板 ${creationTemplates.length + 1}`,
      brief: [
        blueprint.logline,
        blueprint.premise,
        blueprint.core_conflict ? `核心冲突：${blueprint.core_conflict}` : '',
        blueprint.world_hook ? `世界钩子：${blueprint.world_hook}` : '',
        blueprint.protagonist?.name ? `主角：${blueprint.protagonist.name}` : '',
      ].filter(Boolean).join('\n'),
      creative_slots: blueprint.creative_slots,
    }
    const next = [nextTemplate, ...creationTemplates].slice(0, 12)
    localStorage.setItem(CREATION_TEMPLATE_KEY, JSON.stringify(next))
    setCreationTemplates(next)
    message.success('已保存为新书创作模板')
  }

  const openSlotEditor = (blueprint: NovelBlueprint, index: number) => {
    setSlotBlueprintIndex(index)
    setSlotDraft({ ...(blueprint.creative_slots || {}) })
    setSlotEditorOpen(true)
  }

  const closeSlotEditor = () => {
    setSlotEditorOpen(false)
    setSlotBlueprintIndex(null)
    setSlotDraft({})
  }

  const submitSlotEditor = async () => {
    const feedback = `请按以下创意槽调整当前方案：\n${slotDraftToFeedback(slotDraft)}`
    closeSlotEditor()
    await handleReviseBlueprints('refine', feedback)
  }

  const attachImportFile = (file: File) => {
    setPendingImportFile(file)
    const currentTitle = form.getFieldValue('title')
    if (!currentTitle) {
      form.setFieldsValue({ title: titleFromFile(file) })
    }
    return false
  }

  const importFileIntoProject = async (projectId: string, file: File) => {
    setImportStatus('正在解析文件...')
    const formData = new FormData()
    formData.append('file', file)
    const uploadRes = await fetch(`/api/v1/projects/${projectId}/import/file`, {
      method: 'POST',
      body: formData,
    })
    if (!uploadRes.ok) throw new Error('文件解析失败')
    const uploadData = await uploadRes.json() as ApiResponse<UploadResult>
    if (uploadData.code !== 0 || !uploadData.data?.text) {
      throw new Error(uploadData.message || '文件解析失败')
    }

    let splits: SplitItem[] = []
    if (uploadData.data.text.length >= 100) {
      try {
        setImportStatus('正在识别章节...')
        const splitRes = await apiClient.post<ApiResponse<SplitResult>>(`/projects/${projectId}/import/preview`, {
          text: uploadData.data.text,
        })
        splits = splitRes.data.data.splits || []
      } catch {
        splits = []
      }
    }

    setImportStatus('正在写入章节...')
    const confirmRes = await apiClient.post<ApiResponse<ConfirmResult>>(`/projects/${projectId}/import/confirm`, {
      text: uploadData.data.text,
      splits,
    })
    return {
      filename: uploadData.data.filename,
      wordCount: uploadData.data.word_count,
      chapterCount: confirmRes.data.data.total,
    }
  }

  const handleCreate = async (values: ProjectFormValues) => {
    const payload: Record<string, unknown> = {
      title: values.title,
      description: values.description || '',
      tags: tagsPayload(values.tags),
    }

    setCreating(true)
    setImportStatus(pendingImportFile ? '正在创建作品...' : '')
    let createdProjectId: string | null = null
    try {
      const project = await createProject(payload)
      if (!project) return
      createdProjectId = project.id

      if (pendingImportFile) {
        try {
          const imported = await importFileIntoProject(project.id, pendingImportFile)
          message.success(`作品已创建，并导入 ${imported.chapterCount} 章`)
        } catch (err: any) {
          message.warning(`作品已创建，但文件导入失败：${err.message || '未知错误'}`)
        }
      } else {
        message.success('作品创建成功')
      }

      await fetchProjects(searchKeyword || undefined)
      setIsCreateModalOpen(false)
      setPendingImportFile(null)
      setImportStatus('')
      form.resetFields()
      navigate(`/project/${project.id}`)
    } finally {
      setCreating(false)
      if (!createdProjectId) setImportStatus('')
    }
  }

  const handleEdit = async (values: ProjectFormValues) => {
    if (!editingProject) return
    const payload: Record<string, unknown> = {
      title: values.title,
      description: values.description || '',
      tags: tagsPayload(values.tags),
    }
    const project = await updateProject(editingProject.id, payload)
    if (project) {
      message.success('作品已更新')
      setIsEditModalOpen(false)
      setEditingProject(null)
    }
  }

  const handleDelete = async (id: string) => {
    const success = await deleteProject(id)
    if (success) {
      message.success('作品已删除')
    }
  }

  const openEditModal = (project: {
    id: string
    title: string
    description?: string
    tags?: string
  }) => {
    setEditingProject(project)
    editForm.setFieldsValue({
      title: project.title,
      description: project.description || '',
      tags: tagsToFormValue(project.tags),
    })
    setIsEditModalOpen(true)
  }

  const renderTags = (tagsStr?: string) => {
    const tags = tagsToFormValue(tagsStr).split('，').filter(Boolean)
    if (!tags.length) return null
    return (
      <Space size={4} style={{ flexWrap: 'wrap' }}>
        {tags.map((tag) => (
          <Tag key={tag} style={{ fontSize: 12 }}>
            {tag}
          </Tag>
        ))}
      </Space>
    )
  }

  const openFileCreate = (file: File) => {
    setAssistantOpen(false)
    openCreateModal({ title: titleFromFile(file) }, file)
    return false
  }

  const handleGenerateBlueprints = async (values: NovelBriefValues) => {
    const userBrief = buildCreationBrief(values)
    setAssistantBusy(true)
    setAssistantRecommendation('')
    setBlueprints([])
    setAssistantMessages([
      { role: 'user', content: userBrief },
      { role: 'assistant', content: '收到，我先按这个设想生成三套方向不同的新书立项方案。' },
    ])
    try {
      const startRes = await apiClient.post<ApiResponse<NovelStartData>>('/novel-creation/start', {
        mode: 'template',
        ...values,
        user_brief: userBrief,
      })
      const sessionId = startRes.data.data.session_id
      setAssistantSessionId(sessionId)
      const draftRes = await apiClient.post<ApiResponse<NovelDraftData>>('/novel-creation/draft', {
        session_id: sessionId,
        execution_mode: 'template',
        user_brief: userBrief,
        enhance_with_llm: false,
      })
      setBlueprints(draftRes.data.data.blueprints || [])
      setAssistantRecommendation(draftRes.data.data.recommendation || '')
      setAssistantMessages((items) => [
        ...items,
        {
          role: 'assistant',
          content: `已生成 ${draftRes.data.data.blueprints?.length || 0} 个方案。你可以直接选一个创建，也可以继续告诉我想加强或删掉的部分。`,
        },
      ])
      message.success('已生成新书方案')
    } catch (err: any) {
      message.error(err.message || '生成新书方案失败')
    } finally {
      setAssistantBusy(false)
    }
  }

  const handleReviseBlueprints = async (
    revisionMode: 'refine' | 'regenerate',
    overrideFeedback?: string,
    enhanceWithLlm = false,
  ) => {
    const feedback = (overrideFeedback ?? assistantDraftText).trim()
    if (revisionMode === 'refine' && !feedback && !enhanceWithLlm) {
      message.warning('先写下你想调整的方向')
      return
    }

    const values = assistantForm.getFieldsValue()
    const userBrief = buildCreationBrief(values)
    const requestFeedback = feedback || '请对当前方案进行深度优化：增强差异化卖点、主角设计、世界规则、黄金三章和长线卷纲。'
    setAssistantBusy(true)
    setAssistantMessages((items) => [
      ...items,
      ...(requestFeedback ? [{ role: 'user' as const, content: requestFeedback }] : []),
      {
        role: 'assistant',
        content: enhanceWithLlm
          ? '我会启动深度优化，这一步会比快速模板慢一些；完成后会保留需求覆盖率和创意槽供你继续改。'
          : revisionMode === 'refine'
          ? '我会保留当前核心方向，按你的反馈调整卖点、前三章、角色关系和卷纲。'
          : '我会基于你的原始需求，重新生成三套不同的方案。',
      },
    ])
    try {
      let sessionId = assistantSessionId
      if (!sessionId) {
        const startRes = await apiClient.post<ApiResponse<NovelStartData>>('/novel-creation/start', {
          mode: 'template',
          ...values,
          user_brief: userBrief,
        })
        sessionId = startRes.data.data.session_id
        setAssistantSessionId(sessionId)
      }
      const draftRes = await apiClient.post<ApiResponse<NovelDraftData>>('/novel-creation/draft', {
        session_id: sessionId,
        execution_mode: 'template',
        user_brief: userBrief,
        feedback: requestFeedback,
        revision_mode: revisionMode,
        enhance_with_llm: enhanceWithLlm,
      })
      setBlueprints(draftRes.data.data.blueprints || [])
      setAssistantRecommendation(draftRes.data.data.recommendation || '')
      setAssistantDraftText('')
      setAssistantMessages((items) => [
        ...items,
        {
          role: 'assistant',
          content: revisionMode === 'refine'
            ? '已在当前基础上调整完成。重点看核心卖点、黄金三章和第一卷压力是否更贴近你的想法。'
            : '已重新生成整套方案。你可以把新旧方向对比一下，再继续微调或直接创建。',
        },
      ])
    } catch (err: any) {
      message.error(err.message || '调整方案失败')
    } finally {
      setAssistantBusy(false)
    }
  }

  const handleApplyBlueprint = async (blueprint: NovelBlueprint, index: number) => {
    if (!assistantSessionId) {
      message.error('缺少创建会话，请重新生成方案')
      return
    }
    setApplyingBlueprintIndex(index)
    try {
      await apiClient.post<ApiResponse<unknown>>('/novel-creation/review', {
        session_id: assistantSessionId,
        execution_mode: 'template',
        blueprint: blueprints,
      })
      const applyRes = await apiClient.post<ApiResponse<NovelApplyData>>('/novel-creation/apply', {
        session_id: assistantSessionId,
        blueprint_index: index,
        mode: 'auto',
        blueprint,
      })
      const projectId = applyRes.data.data.project_id
      await fetchProjects(searchKeyword || undefined)
      message.success('新书项目已创建')
      setAssistantOpen(false)
      setBlueprints([])
      setAssistantRecommendation('')
      navigate(`/project/${projectId}`)
    } catch (err: any) {
      message.error(err.message || '应用新书方案失败')
    } finally {
      setApplyingBlueprintIndex(null)
    }
  }

  const renderCoverage = (blueprint: NovelBlueprint) => {
    const coverage = blueprint.requirement_coverage
    if (!coverage) return null
    const score = coverage.score ?? 0
    const status = score >= 90 ? 'success' : score >= 70 ? 'normal' : 'exception'
    return (
      <div className="assistant-coverage">
        <div className="assistant-coverage-head">
          <Text strong>需求覆盖率</Text>
          <Text type="secondary">{score}%</Text>
        </div>
        <Progress percent={score} size="small" status={status} showInfo={false} />
        {coverage.missing?.length ? (
          <Space wrap size={4} style={{ marginTop: 6 }}>
            {coverage.missing.slice(0, 4).map((item) => (
              <Tag color="orange" key={item}>{item}</Tag>
            ))}
          </Space>
        ) : (
          <Text type="secondary" className="assistant-small-note">关键要求已覆盖</Text>
        )}
        {coverage.warnings?.length ? (
          <div className="assistant-warning-list">
            {coverage.warnings.slice(0, 2).map((item) => (
              <Text type="warning" key={item}>{item}</Text>
            ))}
          </div>
        ) : null}
      </div>
    )
  }

  const renderCreativeSlots = (blueprint: NovelBlueprint, index: number) => {
    const slots = blueprint.creative_slots || {}
    const entries = Object.entries(slots).filter(([, value]) => slotValueToText(value).trim())
    if (!entries.length) return null
    const labels: Record<string, string> = {
      story_engine: '故事发动机',
      genre_fusion: '类型融合',
      protagonist_design: '主角设计',
      world_rules: '世界规则',
      conflict_engine: '冲突发动机',
      reader_promise: '读者承诺',
      scale_plan: '篇幅规划',
      custom_motifs: '创意要素',
      avoid_list: '禁用/避免',
      reference_examples: '参考样例',
    }
    return (
      <Collapse
        size="small"
        ghost
        className="assistant-slot-collapse"
        items={[{
          key: 'slots',
          label: '创意槽',
          children: (
            <Space direction="vertical" size={6} style={{ width: '100%' }}>
              {entries.slice(0, 10).map(([key, value]) => (
                <div key={key} className="assistant-slot-row">
                  <Text type="secondary">{labels[key] || key}</Text>
                  <Text>{slotValueToText(value)}</Text>
                </div>
              ))}
              <Button size="small" icon={<EditOutlined />} onClick={() => openSlotEditor(blueprint, index)}>
                编辑创意槽并调整
              </Button>
            </Space>
          ),
        }]}
      />
    )
  }

  return (
    <PageWrapper>
      <div className="dashboard-bg-pattern" />
      <SystemNav current="dashboard" />

      <div className="dashboard-hero">
        <h1 className="dashboard-hero-title">
          <BookOutlined />
          墨枢
        </h1>
        <p className="dashboard-hero-sub">管理作品，导入长篇，从一个设想创建完整小说项目</p>
      </div>

      <div className="dashboard-actions moshu-animate-in moshu-stagger-1">
        <Input.Search
          placeholder="搜索作品标题或简介"
          allowClear
          enterButton={<><SearchOutlined /> 搜索</>}
          size="large"
          value={searchKeyword}
          onChange={(e) => setSearchKeyword(e.target.value)}
          onSearch={handleSearch}
        />
        <Space wrap>
          <Button
            icon={<RobotOutlined />}
            size="large"
            onClick={openAssistant}
          >
            新书立项助手
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            size="large"
            className="moshu-btn-press"
            onClick={openAssistant}
          >
            创建新作品
          </Button>
        </Space>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" />
          <div style={{ marginTop: 16, color: 'var(--ant-color-text-secondary)', fontSize: 15 }}>加载中...</div>
        </div>
      ) : projects.length === 0 ? (
        <div className="dashboard-empty moshu-animate-fade">
          <Empty
            description={
              searchKeyword
                ? '未找到匹配的作品'
                : '暂无作品。可以创建空项目、上传已有小说，也可以让新书立项助手先生成角色、世界观和大纲。'
            }
          >
            {!searchKeyword && (
              <Space>
                <Button type="primary" icon={<RobotOutlined />} size="large" onClick={openAssistant}>
                  让助手帮我开书
                </Button>
                <Button icon={<PlusOutlined />} size="large" onClick={() => openCreateModal()}>
                  直接创建
                </Button>
              </Space>
            )}
          </Empty>
        </div>
      ) : (
        <div className="dashboard-grid">
          {projects.map((project) => (
            <div key={project.id} className="dashboard-card-wrap">
              <Card
                className="dashboard-card"
                hoverable
                onClick={() => navigate(`/project/${project.id}`)}
                title={
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', minWidth: 0 }}>
                    <span className="dashboard-card-title">{project.title}</span>
                    <Space size={2} onClick={(e) => e.stopPropagation()}>
                      <Button
                        type="text"
                        size="small"
                        aria-label={`编辑 ${project.title}`}
                        icon={<EditOutlined />}
                        onClick={() => openEditModal(project)}
                      />
                      <Button
                        type="text"
                        size="small"
                        danger
                        aria-label={`删除 ${project.title}`}
                        icon={<DeleteOutlined />}
                        onClick={(e) => {
                          e.stopPropagation()
                          Modal.confirm({
                            title: '确认删除作品',
                            content: `确定要删除「${project.title}」吗？删除作品会同时删除关联的角色、大纲、章节等数据，此操作不可恢复。`,
                            okText: '删除',
                            cancelText: '取消',
                            okButtonProps: { danger: true },
                            onOk: () => handleDelete(project.id),
                          })
                        }}
                      />
                    </Space>
                  </div>
                }
              >
                <p className="dashboard-card-desc">
                  {project.description || '暂无简介'}
                </p>
                {renderTags(project.tags)}
                <div className="dashboard-card-meta">
                  <Text type="secondary">更新于 {new Date(project.updated_at).toLocaleDateString('zh-CN')}</Text>
                  <Text type="secondary">{new Date(project.created_at).toLocaleDateString('zh-CN')} 创建</Text>
                </div>
              </Card>
            </div>
          ))}
        </div>
      )}

      <Drawer
        title="新书立项助手"
        open={assistantOpen}
        onClose={() => setAssistantOpen(false)}
        width={blueprints.length > 0 ? 960 : 600}
        styles={{ body: { padding: blueprints.length > 0 ? '16px 20px' : '24px 28px' } }}
      >
        {/* ── Phase 1: Input — no blueprints yet ── */}
        {blueprints.length === 0 && (
          <div className="assistant-input-phase">
            <Alert
              type="info"
              showIcon
              message="从一个想法创建完整小说项目"
              description="助手会生成多个方案供你挑选和调整。选择后自动创建作品、角色、世界观和大纲。"
            />

            <Card size="small" title="告诉墨枢你想写什么">
              <Form
                form={assistantForm}
                layout="vertical"
                onFinish={handleGenerateBlueprints}
                initialValues={{ genre: 'xianxia', target_audience: 'all', platform: 'qidian' }}
              >
                <Form.Item name="genre" label="类型">
                  <Select options={GENRE_OPTIONS} />
                </Form.Item>
                <Form.Item name="target_audience" label="目标读者">
                  <Select options={AUDIENCE_OPTIONS} />
                </Form.Item>
                <Form.Item name="platform" label="发布平台">
                  <Select options={PLATFORM_OPTIONS} />
                </Form.Item>
                {creationTemplates.length > 0 && (
                  <Form.Item name="template_id" label="套用模板">
                    <Select
                      allowClear
                      placeholder="选择之前保存的新书模板"
                      options={creationTemplates.map((item) => ({ label: item.name, value: item.id }))}
                      onChange={handleTemplateChange}
                    />
                  </Form.Item>
                )}
                <Form.Item
                  name="user_brief"
                  label="创作设想"
                  rules={[{ required: true, message: '请写下你的创作设想' }]}
                >
                  <TextArea
                    placeholder="例如：我想写一本女频修仙文，主角是三岁穿越女娃，核心卖点是科学思维修仙和病毒追杀..."
                    autoSize={{ minRows: 4, maxRows: 8 }}
                    showCount
                    maxLength={1000}
                  />
                </Form.Item>
                <Form.Item name="reference_examples" label="参考作品 / 示例风格（可选）">
                  <TextArea
                    placeholder="例如：想要某作品的探索感、某平台的短章钩子、某类主角关系张力..."
                    autoSize={{ minRows: 2, maxRows: 5 }}
                    showCount
                    maxLength={800}
                  />
                </Form.Item>
                <Button type="primary" htmlType="submit" icon={<RobotOutlined />} loading={assistantBusy} block>
                  生成新书方案
                </Button>
              </Form>
            </Card>

            <Card size="small" title="或者导入已有小说文件">
              <Paragraph type="secondary" style={{ marginBottom: 12 }}>
                已有 TXT / DOCX 可先导入，也可以直接创建空作品。
              </Paragraph>
              <Space>
                <Button icon={<PlusOutlined />} onClick={() => openCreateModal()}>
                  直接创建空作品
                </Button>
                <Upload
                  accept=".txt,.docx"
                  maxCount={1}
                  showUploadList={false}
                  beforeUpload={(file) => openFileCreate(file as File)}
                >
                  <Button icon={<FileAddOutlined />}>导入文件</Button>
                </Upload>
              </Space>
            </Card>
          </div>
        )}

        {/* ── Phase 2: Split layout — blueprints + chat side by side ── */}
        {blueprints.length > 0 && (
          <div className="assistant-split">
            {/* Left: Blueprint cards */}
            <div className="assistant-split-blueprints">
              {assistantRecommendation && (
                <Alert
                  type="success"
                  showIcon
                  message={assistantRecommendation}
                  className="assistant-recommendation"
                />
              )}
              <Space direction="vertical" size={14} style={{ width: '100%' }}>
                {blueprints.map((blueprint, index) => (
                  <Card key={`${blueprint.title}-${index}`} size="small" className="assistant-blueprint-card">
                    <Space direction="vertical" size={8} style={{ width: '100%' }}>
                      <Title level={5} style={{ margin: 0 }}>{blueprint.title}</Title>
                      {blueprint.subtitle && <Tag color="purple" style={{ width: 'fit-content' }}>{blueprint.subtitle}</Tag>}
                      {blueprint.logline && (
                        <Paragraph strong style={{ marginBottom: 0 }}>{blueprint.logline}</Paragraph>
                      )}
                      <Paragraph style={{ marginBottom: 0 }}>{blueprint.premise}</Paragraph>
                      <Space wrap>
                        {blueprint.genre && <Tag>{blueprint.genre}</Tag>}
                        {blueprint.estimated_chapters && <Tag>{blueprint.estimated_chapters} 章预估</Tag>}
                        {blueprint.creation_engine && (
                          <Tag color={blueprint.creation_engine === 'instant_template' ? 'default' : 'purple'}>
                            {blueprint.creation_engine === 'instant_template' ? '快速草案' : '深度优化'}
                          </Tag>
                        )}
                        {blueprint.protagonist?.name && <Tag color="blue">主角：{blueprint.protagonist.name}</Tag>}
                        {blueprint.characters?.length ? <Tag color="cyan">角色 {blueprint.characters.length + 1} 人</Tag> : null}
                        {blueprint.relationships?.length ? <Tag color="orange">关系 {blueprint.relationships.length} 条</Tag> : null}
                        {blueprint.worldbuilding?.length ? <Tag color="geekblue">设定 {blueprint.worldbuilding.length} 条</Tag> : null}
                        {blueprint.volume_outline?.length ? <Tag color="volcano">卷纲 {blueprint.volume_outline.length} 卷</Tag> : null}
                        {blueprint.outline?.length ? <Tag color="green">大纲 {blueprint.outline.length} 节点</Tag> : null}
                      </Space>
                      {renderCoverage(blueprint)}
                      {blueprint.selling_points?.length ? (
                        <div>
                          <Text strong>核心卖点</Text>
                          <ul style={{ margin: '6px 0 0 18px', padding: 0 }}>
                            {blueprint.selling_points.slice(0, 4).map((point, pointIndex) => (
                              <li key={`${blueprint.title}-point-${pointIndex}`}>
                                <Text type="secondary">{point}</Text>
                              </li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                      {blueprint.world_hook && (
                        <Text type="secondary">世界钩子：{blueprint.world_hook}</Text>
                      )}
                      {blueprint.core_conflict && (
                        <Text type="secondary">核心冲突：{blueprint.core_conflict}</Text>
                      )}
                      {blueprint.protagonist?.goal && (
                        <Text type="secondary">主角目标：{blueprint.protagonist.goal}</Text>
                      )}
                      {blueprint.golden_three && (
                        <div>
                          <Text strong>黄金三章</Text>
                          <Space direction="vertical" size={2} style={{ width: '100%', marginTop: 4 }}>
                            {blueprint.golden_three.chapter_1 && <Text type="secondary">1：{blueprint.golden_three.chapter_1}</Text>}
                            {blueprint.golden_three.chapter_2 && <Text type="secondary">2：{blueprint.golden_three.chapter_2}</Text>}
                            {blueprint.golden_three.chapter_3 && <Text type="secondary">3：{blueprint.golden_three.chapter_3}</Text>}
                          </Space>
                        </div>
                      )}
                      {renderCreativeSlots(blueprint, index)}
                      {blueprint.quality_self_check?.issues?.length ? (
                        <Alert
                          type={blueprint.quality_self_check.pass ? 'info' : 'warning'}
                          showIcon
                          message={`自检分 ${blueprint.quality_self_check.score ?? 0}`}
                          description={blueprint.quality_self_check.issues.slice(0, 2).join('；')}
                        />
                      ) : null}
                      <Space wrap>
                        <Button
                          type="primary"
                          onClick={() => handleApplyBlueprint(blueprint, index)}
                          loading={applyingBlueprintIndex === index}
                        >
                          使用这个方案创建
                        </Button>
                        <Button icon={<SaveOutlined />} onClick={() => saveBlueprintAsTemplate(blueprint)}>
                          保存为模板
                        </Button>
                      </Space>
                    </Space>
                  </Card>
                ))}
              </Space>
            </div>

            {/* Right: Sticky chat panel */}
            <div className="assistant-split-chat">
              <div className="assistant-chat-header">
                <RobotOutlined />
                助手对话
              </div>

              <div className="assistant-chat-messages">
                {assistantMessages.map((item, index) => (
                  <div
                    key={`${item.role}-${index}`}
                    className={`assistant-chat-bubble ${
                      item.role === 'user' ? 'assistant-chat-bubble-user' : 'assistant-chat-bubble-assistant'
                    }`}
                  >
                    {item.content}
                  </div>
                ))}
              </div>

              <div className="assistant-chat-input-area">
                <TextArea
                  value={assistantDraftText}
                  onChange={(event) => setAssistantDraftText(event.target.value)}
                  placeholder="告诉助手怎么调整：更暗黑、主角别太被动、换一个开局..."
                  autoSize={{ minRows: 2, maxRows: 4 }}
                  maxLength={800}
                  showCount
                  style={{ fontSize: 13 }}
                />
                <Space direction="vertical" size={6} style={{ width: '100%' }}>
                  <Button
                    type="primary"
                    icon={<RobotOutlined />}
                    loading={assistantBusy}
                    block
                    size="small"
                    onClick={() => handleReviseBlueprints('refine')}
                  >
                    基于当前方案调整
                  </Button>
                  <Button
                    danger
                    loading={assistantBusy}
                    block
                    size="small"
                    onClick={() => handleReviseBlueprints('regenerate')}
                  >
                    全部重新生成
                  </Button>
                  <Divider style={{ margin: '4px 0' }} />
                  <Button
                    loading={assistantBusy}
                    block
                    size="small"
                    onClick={() => handleReviseBlueprints('refine', undefined, true)}
                  >
                    深度优化当前方案
                  </Button>
                </Space>
              </div>
            </div>
          </div>
        )}
      </Drawer>

      <Modal
        title={`编辑创意槽${slotBlueprintIndex !== null ? ` · 方案 ${slotBlueprintIndex + 1}` : ''}`}
        open={slotEditorOpen}
        onCancel={closeSlotEditor}
        onOk={submitSlotEditor}
        okText="按创意槽调整"
        cancelText="取消"
        okButtonProps={{ autoInsertSpace: false, loading: assistantBusy }}
        cancelButtonProps={{ autoInsertSpace: false }}
        width={680}
        destroyOnClose
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="这里改的是创作方向，不会直接覆盖原方案"
            description="确认后，助手会把这些槽位作为反馈重新调整三套方案。"
          />
          {[
            ['story_engine', '故事发动机'],
            ['genre_fusion', '类型融合'],
            ['protagonist_design', '主角设计'],
            ['world_rules', '世界规则'],
            ['conflict_engine', '冲突发动机'],
            ['reader_promise', '读者承诺'],
            ['scale_plan', '篇幅规划'],
            ['custom_motifs', '创意要素'],
            ['avoid_list', '禁用/避免'],
            ['reference_examples', '参考样例'],
          ].map(([key, label]) => (
            <div key={key}>
              <Text strong>{label}</Text>
              <TextArea
                value={slotValueToText(slotDraft[key])}
                onChange={(event) => setSlotDraft((prev) => ({ ...prev, [key]: event.target.value }))}
                autoSize={{ minRows: 2, maxRows: 5 }}
                style={{ marginTop: 6 }}
              />
            </div>
          ))}
        </Space>
      </Modal>

      <Modal
        title="创建作品"
        open={isCreateModalOpen}
        onCancel={closeCreateModal}
        onOk={() => form.submit()}
        okText={pendingImportFile ? '创建并导入' : '创建'}
        cancelText="取消"
        okButtonProps={{ autoInsertSpace: false, loading: creating }}
        cancelButtonProps={{ autoInsertSpace: false, disabled: creating }}
        closable={!creating}
        width={720}
        maskClosable={!creating}
        destroyOnClose
        transitionName=""
        maskTransitionName=""
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item
            name="title"
            label="作品标题"
            rules={[{ required: true, message: '请输入作品标题' }]}
          >
            <Input placeholder="请输入作品标题" maxLength={200} showCount />
          </Form.Item>
          <Form.Item name="description" label="作品简介">
            <TextArea placeholder="写下核心卖点、主角设定或创作方向" rows={4} maxLength={500} showCount />
          </Form.Item>
          <Form.Item name="tags" label="类型标签">
            <Input placeholder="多个标签用逗号分隔，如：玄幻，修仙，热血" />
          </Form.Item>
          <Form.Item label="导入已有小说（可选）">
            <Upload
              accept=".txt,.docx"
              maxCount={1}
              fileList={pendingUploadList}
              beforeUpload={(file) => attachImportFile(file as File)}
              onRemove={() => {
                setPendingImportFile(null)
                return true
              }}
            >
              <Button icon={<UploadOutlined />}>选择 TXT / DOCX 文件</Button>
            </Upload>
            <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
              创建成功后会自动解析章节并写入当前作品。若导入失败，作品仍会保留。
            </Text>
          </Form.Item>
          {importStatus && (
            <Alert type="info" showIcon message={importStatus} style={{ marginTop: 8 }} />
          )}
        </Form>
      </Modal>

      <Modal
        title="编辑作品"
        open={isEditModalOpen}
        onCancel={() => {
          setIsEditModalOpen(false)
          setEditingProject(null)
        }}
        onOk={() => editForm.submit()}
        okText="保存"
        cancelText="取消"
        okButtonProps={{ autoInsertSpace: false }}
        cancelButtonProps={{ autoInsertSpace: false }}
        transitionName=""
        maskTransitionName=""
      >
        <Form form={editForm} layout="vertical" onFinish={handleEdit}>
          <Form.Item
            name="title"
            label="作品标题"
            rules={[{ required: true, message: '请输入作品标题' }]}
          >
            <Input placeholder="请输入作品标题" maxLength={200} showCount />
          </Form.Item>
          <Form.Item name="description" label="作品简介">
            <TextArea placeholder="请输入作品简介" rows={3} maxLength={500} showCount />
          </Form.Item>
          <Form.Item name="tags" label="类型标签">
            <Input placeholder="多个标签用逗号分隔，如：玄幻，修仙，热血" />
          </Form.Item>
        </Form>
      </Modal>
    </PageWrapper>
  )
}

export default DashboardPage
