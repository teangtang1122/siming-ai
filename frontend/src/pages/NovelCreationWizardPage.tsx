import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Alert,
  Badge,
  Button,
  Card,
  Collapse,
  Descriptions,
  Divider,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Radio,
  Select,
  Space,
  Spin,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  BookOutlined,
  CloudSyncOutlined,
  DeleteOutlined,
  EditOutlined,
  ExperimentOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RocketOutlined,
  SaveOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import SystemNav from '../components/SystemNav'
import {
  FlowNavigator,
} from '../components/interaction'
import { StageActionBar, StageFeedback } from '../components/novel-creation'
import { apiClient } from '../api/client'
import { useModelOptions } from '../hooks/useModelOptions'
import {
  startNovelCreationConceptRun,
  startNovelCreationSession,
  workbenchUrl,
} from '../hooks/useNovelCreationInterviewController'
import './NovelCreationWizardPage.css'

const { Paragraph, Text, Title } = Typography
const { TextArea } = Input

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface PresetDefaults {
  world_tone: string
  story_structure: string
  pacing: string
  writing_style: string
  special_requirements: string[]
  avoid: string[]
}

interface GenrePreset {
  id: string
  label: string
  description: string
  themes: Array<{ id: string; label: string }>
  defaults: PresetDefaults
}

interface PresetCatalog {
  categories: GenrePreset[]
  platforms: string[]
  audiences: string[]
  length_options: Array<{ id: string; label: string; words: number; chapters: number }>
  stage_order: string[]
  stage_labels: Record<string, string>
}

interface ConceptCard {
  id: string
  title: string
  subtitle?: string
  logline: string
  source_index: number
  protagonist_seed: { name: string; identity: string; goal: string; lack: string }
  world_hook: string
  core_conflict: string
  story_engine: string
  opening_hook: string
  differentiators: string[]
  risks: string[]
  coverage: { score: number; covered: string[]; missing: string[] }
}

interface StageState {
  status: 'pending' | 'generated' | 'confirmed' | 'stale'
  data?: Record<string, unknown> | null
  source?: string
  stale_reason?: string
  updated_at?: string
}

interface StageFlowItem {
  stage: string
  label: string
  status: StageState['status']
  can_view: boolean
  can_generate: boolean
  can_confirm: boolean
  blocked_by: Array<{ stage: string; label: string; reason: string }>
  actions: string[]
  next_stage?: string | null
}

interface StageFlow {
  attention_stage?: string | null
  recommended_stage?: string | null
  legacy_current_stage?: string | null
  pending_confirmations: string[]
  items: Record<string, StageFlowItem>
}

interface CreationFormValues {
  brief: string
  preset_id: string
  theme_id?: string
  genre: string
  target_audience: string
  platform: string
  target_words: number
  target_chapters: number
  world_tone: string
  story_structure: string
  pacing: string
  writing_style: string
  special_requirements: string[]
  avoid: string[]
}

interface CreationSession {
  id: string
  status: string
  current_stage?: string
  created_project_id?: string
  revision: number
  stage_flow?: StageFlow
  updated_at?: string
  last_error?: {
    failure_class?: string
    message?: string
    next_action?: string
    failed_stage?: string
    failed_stage_label?: string
  }
  runs?: StageRun[]
  draft?: {
    form: CreationFormValues
    concepts: ConceptCard[]
    selected_concept_id?: string
    quick_mode?: boolean
    stages: Record<string, StageState>
  }
}

interface StageRun {
  id: string
  session_id?: string
  stage: string
  status: string
  current_message?: string
  failure_class?: string
  next_action?: string
  operation_id?: string
  input_revision?: number
  input_snapshot_hash?: string
  model_source?: string
  events?: Array<{
    event_type: string
    status?: string
    message?: string
    payload?: Record<string, unknown>
  }>
}

const CORE_STAGES = ['world_style', 'characters', 'locations', 'macro_outline', 'opening_outline', 'final_review']

function errorText(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请稍后重试'
}

function splitLines(value: string) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

function stageTone(status?: StageState['status']) {
  if (status === 'confirmed') return 'success'
  if (status === 'stale') return 'warning'
  if (status === 'generated') return 'processing'
  return 'default'
}

function stageStatusLabel(status?: StageState['status']) {
  const labels: Record<string, string> = {
    pending: '待生成',
    generated: '待确认',
    confirmed: '已确认',
    stale: '需重新校验',
  }
  return labels[status || 'pending'] || '待生成'
}

const stageFieldLabels: Record<string, string> = {
  writing_style: '正文风格', world_tone: '世界基调', story_structure: '剧情结构', pacing: '叙事节奏',
  style_rules: '文风规则', forbidden_patterns: '避雷项', worldbuilding: '世界设定', display_groups: '展示分组',
  characters: '角色', relationships: '角色关系', entries: '地点与势力', relations: '稳定关系',
  story_overview: '故事总览', core_conflict: '核心冲突', ending_direction: '结局方向', target_chapters: '目标章节数',
  volumes: '分卷规划', stage_plan: '阶段规划', chapters: '章节细纲', sections: '场景事件',
  title: '标题', name: '名称', summary: '摘要', content: '内容', description: '说明', dimension: '维度',
  role_type: '角色类型', background: '背景', personality: '性格', goal: '目标', current_goal: '当前目标', profile: '写作锁',
  source_title: '起点', target_title: '终点', relation_type: '关系类型', start_chapter: '起始章节', end_chapter: '结束章节',
  client_id: '内部标识', parent_client_id: '所属章节', metadata: '场景信息', ready: '可以创建', blocking: '阻塞项', warnings: '提醒', counts: '数量检查',
  core_tone: '核心基调', atmosphere: '氛围', emotional_color: '情绪色彩', reader_experience: '读者感受',
  narrative_perspective: '叙事视角', perspective: '叙事视角', sentence_rhythm: '句式节奏', language_style: '语言风格',
  main_line: '主线结构', stages: '阶段安排', opening: '开篇节奏', middle: '中段节奏', climax: '高潮节奏',
}

function fieldLabel(key: string) {
  return stageFieldLabels[key] || key.replace(/_/g, ' ')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function recordRows(value: unknown, nameField = 'title'): Array<Record<string, unknown>> {
  if (Array.isArray(value)) return value.filter(isRecord)
  if (!isRecord(value)) return []
  return Object.entries(value).flatMap(([name, child]) => {
    if (!isRecord(child)) return []
    return [{ ...child, [nameField]: child[nameField] || name }]
  })
}

function uniqueRows(rows: Array<Record<string, unknown>>, keyBuilder: (item: Record<string, unknown>) => string) {
  const seen = new Set<string>()
  return rows.filter((item) => {
    const key = keyBuilder(item)
    if (!key || !seen.has(key)) {
      if (key) seen.add(key)
      return true
    }
    return false
  })
}

function roleTypeLabel(value: unknown, index: number) {
  const role = String(value || (index === 0 ? 'protagonist' : 'supporting'))
  return ({ protagonist: '主角', supporting: '配角', antagonist: '对手' } as Record<string, string>)[role] || role
}

function volumeRange(item: Record<string, unknown>) {
  if (item.start_chapter && item.end_chapter) return `${String(item.start_chapter)} - ${String(item.end_chapter)} 章`
  const numbers = String(item.chapters || '').match(/\d+/g)
  if (numbers && numbers.length >= 2) return `${numbers[0]} - ${numbers[1]} 章`
  return '章节范围待确认'
}

function collectionItemLabel(value: unknown, index: number) {
  if (!isRecord(value)) return `第 ${index + 1} 项`
  return String(value.title || value.name || value.client_id || value.source_title || `第 ${index + 1} 项`)
}

function StructuredPreviewValue({ value }: { value: unknown }) {
  if (value == null || value === '') {
    return <Text type="secondary">未提供</Text>
  }
  if (typeof value === 'boolean') {
    return <span className="creation-preview-value">{value ? '是' : '否'}</span>
  }
  if (typeof value === 'string' || typeof value === 'number') {
    return <span className="creation-preview-value">{String(value)}</span>
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <Text type="secondary">未提供</Text>
    return (
      <ul className="creation-preview-list">
        {value.map((item, index) => (
          <li key={`${collectionItemLabel(item, index)}-${index}`}>
            <StructuredPreviewValue value={item} />
          </li>
        ))}
      </ul>
    )
  }
  if (isRecord(value)) {
    const entries = Object.entries(value)
    if (entries.length === 0) return <Text type="secondary">未提供</Text>
    return (
      <dl className="creation-preview-fields">
        {entries.map(([key, child]) => (
          <div className="creation-preview-field" key={key}>
            <dt>{fieldLabel(key)}</dt>
            <dd><StructuredPreviewValue value={child} /></dd>
          </div>
        ))}
      </dl>
    )
  }
  return <span className="creation-preview-value">{String(value)}</span>
}

function StructuredValueEditor({ fieldKey, value, onChange }: { fieldKey: string; value: unknown; onChange: (value: unknown) => void }) {
  const label = fieldLabel(fieldKey)

  if (typeof value === 'boolean') {
    return <Radio.Group aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}><Radio.Button value>是</Radio.Button><Radio.Button value={false}>否</Radio.Button></Radio.Group>
  }
  if (typeof value === 'number') {
    return <InputNumber aria-label={label} value={value} onChange={(next) => onChange(next ?? 0)} style={{ width: '100%' }} />
  }
  if (typeof value === 'string' || value == null) {
    const text = value == null ? '' : value
    const multiline = text.length > 80 || ['summary', 'content', 'description', 'background', 'story_overview', 'core_conflict', 'ending_direction'].includes(fieldKey)
    return multiline
      ? <TextArea aria-label={label} value={text} rows={3} onChange={(event) => onChange(event.target.value)} />
      : <Input aria-label={label} value={text} onChange={(event) => onChange(event.target.value)} />
  }
  if (Array.isArray(value)) {
    const onlySimpleValues = value.every((item) => ['string', 'number', 'boolean'].includes(typeof item))
    if (onlySimpleValues) {
      return <TextArea aria-label={label} value={value.map(String).join('\n')} rows={Math.min(6, Math.max(2, value.length + 1))} placeholder="每行一项" onChange={(event) => onChange(splitLines(event.target.value))} />
    }
    if (value.length === 0) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本组暂无内容，可在高级编辑中补充" />
    return (
      <Collapse
        size="small"
        className="creation-structured-collection"
        items={value.map((item, index) => ({
          key: `${fieldKey}-${index}`,
          label: collectionItemLabel(item, index),
          children: <StructuredValueEditor fieldKey={`${fieldKey}_${index + 1}`} value={item} onChange={(next) => { const updated = [...value]; updated[index] = next; onChange(updated) }} />,
        }))}
      />
    )
  }
  if (isRecord(value)) {
    return (
      <div className="creation-structured-fields">
        {Object.entries(value).map(([key, child]) => (
          <div className="creation-structured-field" key={key}>
            <Text strong>{fieldLabel(key)}</Text>
            <StructuredValueEditor fieldKey={key} value={child} onChange={(next) => onChange({ ...value, [key]: next })} />
          </div>
        ))}
      </div>
    )
  }
  return <Text type="secondary">暂不支持直接编辑此字段</Text>
}

function StructuredStageEditor({ data, onChange }: { data: Record<string, unknown>; onChange: (data: Record<string, unknown>) => void }) {
  return <StructuredValueEditor fieldKey="stage" value={data} onChange={(next) => onChange(isRecord(next) ? next : data)} />
}

function StagePreview({ stage, data }: { stage: string; data?: Record<string, unknown> | null }) {
  if (!data) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本阶段尚未生成" />

  if (stage === 'world_style') {
    const world = recordRows(data.worldbuilding)
    return (
      <div className="creation-stage-preview">
        <Descriptions column={{ xs: 1, sm: 1, md: 2, lg: 2, xl: 2, xxl: 2 }} size="small" bordered>
          <Descriptions.Item label="世界基调"><StructuredPreviewValue value={data.world_tone} /></Descriptions.Item>
          <Descriptions.Item label="正文风格"><StructuredPreviewValue value={data.writing_style} /></Descriptions.Item>
          <Descriptions.Item label="结构"><StructuredPreviewValue value={data.story_structure} /></Descriptions.Item>
          <Descriptions.Item label="节奏"><StructuredPreviewValue value={data.pacing} /></Descriptions.Item>
        </Descriptions>
        <div className="creation-item-grid">
          {world.map((item, index) => (
            <Card key={`${String(item.title)}-${index}`} size="small" title={String(item.title || `设定 ${index + 1}`)} extra={<Tag>{String(item.dimension || 'culture')}</Tag>}>
              <div className="creation-worldbuilding-content">
                <StructuredPreviewValue value={item.content || item.description} />
              </div>
            </Card>
          ))}
        </div>
      </div>
    )
  }

  if (stage === 'characters') {
    const characters = recordRows(data.characters, 'name')
    return (
      <div className="creation-item-grid creation-character-grid">
        {characters.map((item, index) => {
          const profile = (item.profile || {}) as Record<string, unknown>
          return (
            <Card key={`${String(item.name)}-${index}`} size="small" title={String(item.name || `角色 ${index + 1}`)} extra={<Tag>{roleTypeLabel(item.role_type, index)}</Tag>}>
              <Paragraph>{String(item.background || item.personality || '')}</Paragraph>
              <Descriptions column={1} size="small">
                <Descriptions.Item label="当前目标">{String(item.goal || item.current_goal || profile.core_motivation || '待补充')}</Descriptions.Item>
                <Descriptions.Item label="核心动机">{String(profile.core_motivation || '')}</Descriptions.Item>
                <Descriptions.Item label="内在缺口">{String(profile.inner_lack || '')}</Descriptions.Item>
                <Descriptions.Item label="声线">{String(profile.voice || '')}</Descriptions.Item>
              </Descriptions>
            </Card>
          )
        })}
      </div>
    )
  }

  if (stage === 'locations') {
    const entries = uniqueRows(recordRows(data.entries), (item) => String(item.title || '').trim().toLocaleLowerCase())
    const relations = uniqueRows(recordRows(data.relations), (item) => [item.source_title, item.target_title, item.relation_type].map((value) => String(value || '').trim().toLocaleLowerCase()).join('|'))
    return (
      <div className="creation-stage-preview">
        <div className="creation-item-grid">
          {entries.map((item, index) => <Card size="small" key={`${String(item.title)}-${index}`} title={String(item.title || `地点 ${index + 1}`)}><Paragraph>{String(item.content || item.description || '')}</Paragraph></Card>)}
        </div>
        <Divider orientation="left">稳定关系</Divider>
        <Timeline items={relations.map((item) => ({ children: <><Text strong>{String(item.source_title)}</Text> <Text type="secondary">{String(item.relation_type)}</Text> <Text strong>{String(item.target_title)}</Text><br /><Text type="secondary">{String(item.description || '')}</Text></> }))} />
      </div>
    )
  }

  if (stage === 'macro_outline') {
    const volumes = recordRows(data.volumes)
    return (
      <div className="creation-stage-preview">
        <Alert type="info" showIcon message={String(data.core_conflict || '')} description={String(data.story_overview || '')} />
        <Timeline className="creation-volume-timeline" items={volumes.map((item) => ({
          children: <div><Text strong>{String(item.title || '')}</Text><Tag style={{ marginLeft: 8 }}>{volumeRange(item)}</Tag><Paragraph>{String(item.summary || item.core_function || item.focus || '')}</Paragraph></div>,
        }))} />
      </div>
    )
  }

  if (stage === 'opening_outline') {
    const chapters = Array.isArray(data.chapters) ? data.chapters as Array<Record<string, unknown>> : []
    const sections = Array.isArray(data.sections) ? data.sections as Array<Record<string, unknown>> : []
    return (
      <Collapse
        className="creation-outline-collapse"
        items={chapters.map((chapter) => {
          const childSections = sections.filter((section) => section.parent_client_id === chapter.client_id)
          return {
            key: String(chapter.client_id),
            label: <Space><Text strong>{String(chapter.title)}</Text><Tag>{childSections.length} 个场景</Tag></Space>,
            children: <><Paragraph>{String(chapter.summary || '')}</Paragraph>{childSections.map((section, index) => {
              const metadata = (section.metadata || {}) as Record<string, unknown>
              return <div className="creation-section-row" key={`${String(section.client_id)}-${index}`}><Badge count={index + 1} color="var(--ant-color-primary)" /><div><Text strong>{String(section.title)}</Text><Paragraph type="secondary">{String(metadata.purpose || section.summary || '')}</Paragraph><Space wrap size={4}><Tag>{String(metadata.location || '地点待定')}</Tag><Tag>{String(metadata.pov_character || '视角待定')}</Tag><Tag>{String(metadata.exit_state || '状态待定')}</Tag></Space></div></div>
            })}</>,
          }
        })}
      />
    )
  }

  if (stage === 'final_review') {
    const counts = (data.counts || {}) as Record<string, unknown>
    const blocking = Array.isArray(data.blocking) ? data.blocking as string[] : []
    const warnings = Array.isArray(data.warnings) ? data.warnings as string[] : []
    return (
      <div className="creation-final-review">
        <Alert type={data.ready ? 'success' : 'error'} showIcon message={data.ready ? '立项档案已达到创建标准' : '还不能创建正式作品'} description={blocking.join('；') || '所有关键阶段和颗粒度检查均已通过。'} />
        <div className="creation-count-grid">
          {Object.entries(counts).map(([key, value]) => <div key={key}><strong>{String(value)}</strong><span>{({ characters: '角色', worldbuilding: '世界设定', chapters: '细纲章节', sections: '场景事件' } as Record<string, string>)[key] || key}</span></div>)}
        </div>
        {warnings.map((warning) => <Alert key={warning} type="warning" showIcon message={warning} />)}
      </div>
    )
  }

  return <pre className="creation-json-preview">{JSON.stringify(data, null, 2)}</pre>
}

function NovelCreationWizardPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [form] = Form.useForm<CreationFormValues>()
  const { modelOptions, defaultModel, hasModels, loading: modelsLoading } = useModelOptions()
  const [catalog, setCatalog] = useState<PresetCatalog | null>(null)
  const [sessions, setSessions] = useState<CreationSession[]>([])
  const [session, setSession] = useState<CreationSession | null>(null)
  const [selectedModel, setSelectedModel] = useState<string>()
  const [busy, setBusy] = useState(false)
  const [runMessage, setRunMessage] = useState('')
  const [runProgress, setRunProgress] = useState(0)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editorText, setEditorText] = useState('')
  const [editorData, setEditorData] = useState<Record<string, unknown>>({})
  const [presetSearch, setPresetSearch] = useState('')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [formEditTick, setFormEditTick] = useState(0)
  const [saveNotice, setSaveNotice] = useState('')
  const [runConnection, setRunConnection] = useState<'connected' | 'reconnecting'>('connected')
  const [activeRun, setActiveRun] = useState<StageRun | null>(null)
  const [resultRevisionNotice, setResultRevisionNotice] = useState('')
  const [stageActionError, setStageActionError] = useState('')
  const watchingRunRef = useRef<string | null>(null)
  const stageHeadingRef = useRef<HTMLDivElement | null>(null)
  const defaultsAppliedRef = useRef(false)
  const loadRequestRef = useRef(0)
  const saveRequestRef = useRef(0)
  const loadedSessionIdRef = useRef<string | null>(null)
  const hydratingFormRef = useRef(false)
  const formDirtyRef = useRef(false)
  const editTickRef = useRef(0)
  const editedDuringRunRef = useRef(false)

  const requestedSessionId = searchParams.get('session') || undefined
  const requestedRunId = searchParams.get('run')
  const requestedModel = searchParams.get('model') || undefined
  const requestedStage = searchParams.get('stage') || undefined

  const watchedPresetId = Form.useWatch('preset_id', form)
  const activePreset = useMemo(() => catalog?.categories.find((item) => item.id === watchedPresetId), [catalog, watchedPresetId])
  const concepts = session?.draft?.concepts || []
  const selectedConceptId = session?.draft?.selected_concept_id
  const attentionStage = session?.stage_flow?.attention_stage
  const currentStage = (
    requestedStage
    && CORE_STAGES.includes(requestedStage)
    && session?.stage_flow?.items?.[requestedStage]?.can_view
  )
    ? requestedStage
    : attentionStage && CORE_STAGES.includes(attentionStage)
      ? attentionStage
      : session?.current_stage && CORE_STAGES.includes(session.current_stage)
        ? session.current_stage
        : 'world_style'
  const currentStageState = session?.draft?.stages?.[currentStage]
  const currentStageFlow = session?.stage_flow?.items?.[currentStage]
  const recommendedStage = session?.stage_flow?.recommended_stage
  const nextStage = currentStageFlow?.next_stage && CORE_STAGES.includes(currentStageFlow.next_stage)
    ? currentStageFlow.next_stage
    : undefined
  const stageLabels = catalog?.stage_labels || {}
  const filteredPresets = useMemo(() => {
    const keyword = presetSearch.trim().toLowerCase()
    if (!keyword) return catalog?.categories || []
    return (catalog?.categories || []).filter((item) => `${item.label}${item.description}${item.themes.map((theme) => theme.label).join('')}`.toLowerCase().includes(keyword))
  }, [catalog, presetSearch])
  const otherSessions = useMemo(() => sessions.filter((item) => item.id !== session?.id), [sessions, session?.id])

  const loadSessions = useCallback(async () => {
    const response = await apiClient.get<ApiResponse<{ sessions: CreationSession[] }>>('/novel-creation/sessions')
    setSessions(response.data.data.sessions || [])
  }, [])

  const loadSession = useCallback(async (sessionId: string) => {
    const requestId = ++loadRequestRef.current
    const response = await apiClient.get<ApiResponse<CreationSession>>(`/novel-creation/sessions/${sessionId}`)
    const loaded = response.data.data
    if (requestId !== loadRequestRef.current) return loaded
    setSession(loaded)
    const switchingSession = loadedSessionIdRef.current !== loaded.id
    if (loaded.draft?.form && (switchingSession || !formDirtyRef.current)) {
      hydratingFormRef.current = true
      form.setFieldsValue(loaded.draft.form)
      hydratingFormRef.current = false
      formDirtyRef.current = false
    }
    loadedSessionIdRef.current = loaded.id
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.set('session', loaded.id)
      const existingStage = next.get('stage')
      const flow = loaded.stage_flow
      const targetStage = existingStage && flow?.items?.[existingStage]?.can_view
        ? existingStage
        : flow?.attention_stage || flow?.recommended_stage || loaded.current_stage
      if (targetStage && CORE_STAGES.includes(targetStage)) next.set('stage', targetStage)
      else next.delete('stage')
      if (next.toString() === current.toString()) return current
      return next
    }, { replace: true })
    return loaded
  }, [form, setSearchParams])

  const viewStage = useCallback((stage: string, replace = false) => {
    if (!CORE_STAGES.includes(stage)) return
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.set('stage', stage)
      return next
    }, { replace })
    setStageActionError('')
  }, [setSearchParams])

  const focusStageHeading = useCallback(() => {
    window.requestAnimationFrame(() => stageHeadingRef.current?.focus())
  }, [])

  useEffect(() => {
    let cancelled = false
    const initializeCatalog = async () => {
      try {
        const response = await apiClient.get<ApiResponse<PresetCatalog>>('/novel-creation/presets')
        if (cancelled) return
        setCatalog(response.data.data)
        const first = response.data.data.categories[0]
        if (!requestedSessionId && !defaultsAppliedRef.current) {
          defaultsAppliedRef.current = true
          hydratingFormRef.current = true
          form.setFieldsValue({
            brief: '', preset_id: first?.id || 'free', theme_id: first?.themes[0]?.id,
            genre: first?.label || '自由创作', target_audience: '成年大众', platform: '暂不确定',
            target_words: 600000, target_chapters: 240,
            world_tone: first?.defaults.world_tone || '', story_structure: first?.defaults.story_structure || '',
            pacing: first?.defaults.pacing || '', writing_style: first?.defaults.writing_style || '',
            special_requirements: first?.defaults.special_requirements || [], avoid: first?.defaults.avoid || [],
          })
          hydratingFormRef.current = false
        }
        await loadSessions()
      } catch (error) {
        if (!cancelled) message.error(errorText(error))
      }
    }
    void initializeCatalog()
    return () => { cancelled = true }
  }, [form, loadSessions, requestedSessionId])

  useEffect(() => {
    if (!requestedSessionId) return
    void loadSession(requestedSessionId).catch((error) => message.error(errorText(error)))
  }, [loadSession, requestedSessionId])

  useEffect(() => {
    if (defaultModel && !selectedModel) setSelectedModel(defaultModel)
  }, [defaultModel, selectedModel])

  useEffect(() => {
    if (requestedModel) setSelectedModel(requestedModel)
  }, [requestedModel])

  const markFormEdited = useCallback(() => {
    if (hydratingFormRef.current) return
    formDirtyRef.current = true
    editTickRef.current += 1
    setFormEditTick(editTickRef.current)
    setSaveNotice('修改尚未保存')
    if (watchingRunRef.current) editedDuringRunRef.current = true
  }, [])

  useEffect(() => {
    if (!session || !formDirtyRef.current) return
    const capturedTick = formEditTick
    const requestId = ++saveRequestRef.current
    const timer = window.setTimeout(async () => {
      const values = form.getFieldsValue(true)
      try {
        const response = await apiClient.patch<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}`, {
          form: values,
          expected_revision: session.revision,
        })
        if (requestId !== saveRequestRef.current) return
        setSession(response.data.data)
        if (capturedTick === editTickRef.current) {
          formDirtyRef.current = false
          setSaveNotice('已自动保存为下一版草稿')
        }
      } catch (error) {
        if (requestId !== saveRequestRef.current) return
        const structured = error as Error & { response?: { status?: number } }
        if (structured.response?.status === 409) {
          setSaveNotice('检测到草稿版本变化，本地修改仍保留，正在重新同步')
          try {
            const latest = await apiClient.get<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}`)
            setSession(latest.data.data)
            editTickRef.current += 1
            setFormEditTick(editTickRef.current)
          } catch { /* keep local form authoritative until the next retry */ }
        } else {
          setSaveNotice('自动保存暂时失败，本地修改仍保留')
        }
      }
    }, 800)
    return () => window.clearTimeout(timer)
  }, [form, formEditTick, session])

  const applyPreset = (preset: GenrePreset) => {
    form.setFieldsValue({
      preset_id: preset.id,
      theme_id: preset.themes[0]?.id,
      genre: preset.label,
      world_tone: preset.defaults.world_tone,
      story_structure: preset.defaults.story_structure,
      pacing: preset.defaults.pacing,
      writing_style: preset.defaults.writing_style,
      special_requirements: preset.defaults.special_requirements,
      avoid: preset.defaults.avoid,
    })
    markFormEdited()
  }

  const persistIntake = async () => {
    const values = await form.validateFields()
    ++saveRequestRef.current
    if (session) {
      const save = async (expectedRevision: number) => apiClient.patch<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}`, {
        form: values,
        expected_revision: expectedRevision,
      })
      let response
      try {
        response = await save(session.revision)
      } catch (error) {
        const structured = error as Error & { response?: { status?: number } }
        if (structured.response?.status !== 409) throw error
        const latest = await apiClient.get<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}`)
        response = await save(latest.data.data.revision)
      }
      formDirtyRef.current = false
      setSaveNotice('草稿已保存')
      setSession(response.data.data)
      return response.data.data
    }
    const created = await startNovelCreationSession({
      mode: 'internal_llm',
      userBrief: values.brief,
      form: values,
    })
    const payload = created.raw as { session?: CreationSession }
    const createdSession = payload.session || (await apiClient.get<ApiResponse<CreationSession>>(`/novel-creation/sessions/${created.id}`)).data.data
    setSession(createdSession)
    loadedSessionIdRef.current = createdSession.id
    formDirtyRef.current = false
    setSaveNotice('草稿已保存')
    setSearchParams({ session: created.id }, { replace: true })
    await loadSessions()
    return createdSession
  }

  const saveIntake = async () => {
    setBusy(true)
    try {
      await persistIntake()
      message.success('立项草稿已保存，可随时关闭后继续')
    } catch (error) {
      message.error(errorText(error))
    } finally {
      setBusy(false)
    }
  }

  const generateConcepts = async () => {
    if (!hasModels || !selectedModel) {
      message.warning('还没有可用模型。先到系统设置配置 API 或本机 CLI，并完成连接测试。')
      return
    }
    setBusy(true)
    setRunMessage('正在理解创作约束并生成三套轻量创意...')
    setRunProgress(0)
    setResultRevisionNotice('')
    editedDuringRunRef.current = false
    try {
      const saved = await persistIntake()
      const run = await startNovelCreationConceptRun(saved.id, selectedModel, saved.revision)
      setActiveRun(run)
      const query = workbenchUrl(saved.id, run.id, selectedModel).split('?')[1] || ''
      setSearchParams(new URLSearchParams(query), { replace: true })
      watchRun(run.id)
    } catch (error) {
      setBusy(false)
      setRunMessage('')
      message.error(errorText(error))
    }
  }

  const watchRun = useCallback((runId: string) => {
    if (watchingRunRef.current === runId) return
    watchingRunRef.current = runId
    setBusy(true)
    setRunConnection('connected')
    const source = new EventSource(`/api/v1/novel-creation/runs/${runId}/stream`)
    source.onopen = () => setRunConnection('connected')
    const handleEvent = (event: MessageEvent) => {
      try {
        const payload = JSON.parse(event.data) as { message?: string; event_type?: string; payload?: { stage?: string } }
        if (payload.message) setRunMessage(payload.message)
        const stageIndex = payload.payload?.stage ? CORE_STAGES.indexOf(payload.payload.stage) : -1
        if (stageIndex >= 0) {
          const completed = payload.event_type === 'stage_completed'
          setRunProgress(Math.round(((stageIndex + (completed ? 1 : 0.25)) / CORE_STAGES.length) * 100))
        }
        if (payload.event_type === 'stage_completed') {
          const targetSessionId = requestedSessionId || session?.id
          if (targetSessionId) void loadSession(targetSessionId).catch(() => undefined)
        }
      } catch { /* keep the last readable status */ }
    }
    source.addEventListener('started', handleEvent as EventListener)
    source.addEventListener('stage_progress', handleEvent as EventListener)
    source.addEventListener('stage_repaired', handleEvent as EventListener)
    source.addEventListener('stage_completed', handleEvent as EventListener)
    source.addEventListener('completed', handleEvent as EventListener)
    source.addEventListener('failed', handleEvent as EventListener)
    source.addEventListener('done', (event) => {
      source.close()
      watchingRunRef.current = null
      setRunConnection('connected')
      let finished: StageRun | null = null
      try {
        finished = JSON.parse((event as MessageEvent).data) as StageRun
        setActiveRun(finished)
        if (finished.status === 'failed') {
          message.error(finished.current_message || '阶段生成失败')
        } else {
          message.success('阶段结果已保存到立项草稿')
        }
      } catch { /* the session refresh below is authoritative */ }
      const targetSessionId = finished?.session_id || requestedSessionId || session?.id
      if (finished?.input_revision != null) {
        const suffix = editedDuringRunRef.current ? '；运行期间的新修改已保存为下一版，不会被旧结果覆盖' : ''
        const repairedStages = (finished.events || [])
          .filter((item) => item.event_type === 'stage_repaired')
          .map((item) => String(item.payload?.stage || '未知阶段'))
        const repairNotice = repairedStages.length > 0
          ? `；${repairedStages.length} 个阶段的模型回复不可用，已采用安全结构并保留供你审阅`
          : ''
        setResultRevisionNotice(`本次结果基于草稿 v${finished.input_revision}${suffix}${repairNotice}`)
      }
      if (targetSessionId) {
        void loadSession(targetSessionId).then(() => focusStageHeading())
      }
      setBusy(false)
      setRunMessage('')
      setRunProgress(100)
    })
    source.onerror = () => {
      setRunConnection('reconnecting')
      setRunMessage('进度连接中断，正在重新连接；后台任务仍在运行...')
      void apiClient.get<ApiResponse<StageRun>>(`/novel-creation/runs/${runId}`).then((response) => {
        const current = response.data.data
        setActiveRun(current)
        if (current.current_message) setRunMessage(current.current_message)
      }).catch(() => undefined)
    }
  }, [focusStageHeading, loadSession, requestedSessionId, session?.id])

  useEffect(() => {
    const activeRun = requestedRunId
      ? session?.runs?.find((run) => run.id === requestedRunId && run.status === 'running')
      : session?.runs?.find((run) => run.status === 'running')
    if (!activeRun) return
    setActiveRun(activeRun)
    setRunMessage(activeRun.current_message || '正在恢复立项任务...')
    watchRun(activeRun.id)
  }, [requestedRunId, session?.id, session?.runs, watchRun])

  const startStageRun = async (stage: string, autoConfirm = false, runSession: CreationSession | null = session) => {
    if (!runSession || !selectedModel) return false
    setBusy(true)
    setStageActionError('')
    setRunProgress(0)
    setRunMessage(`正在生成${stage === 'all' ? '完整立项档案' : stageLabels[stage] || stage}...`)
    setResultRevisionNotice('')
    editedDuringRunRef.current = false
    try {
      const response = await apiClient.post<ApiResponse<{ run: StageRun }>>(`/novel-creation/sessions/${runSession.id}/runs`, {
        stage,
        model: selectedModel,
        use_model: true,
        auto_confirm: autoConfirm,
        expected_revision: runSession.revision,
      })
      setActiveRun(response.data.data.run)
      if (stage !== 'all') viewStage(stage)
      watchRun(response.data.data.run.id)
      return true
    } catch (error) {
      setBusy(false)
      setRunMessage('')
      setRunProgress(0)
      setStageActionError(errorText(error))
      message.error(errorText(error))
      return false
    }
  }

  const chooseConcept = async (conceptId: string, quickMode: boolean) => {
    if (!session) return
    setBusy(true)
    try {
      const selection = await apiClient.patch<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}`, {
        selected_concept_id: conceptId,
        quick_mode: quickMode,
        expected_revision: session.revision,
      })
      const constraints = await apiClient.post<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}/stages/constraints/confirm`, {
        data: selection.data.data.draft?.form,
        confirm: true,
        source: 'author',
        expected_revision: selection.data.data.revision,
      })
      const conceptConfirmation = await apiClient.post<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}/stages/concepts/confirm`, {
        data: { options: concepts, selected_concept_id: conceptId },
        confirm: true,
        source: 'author',
        expected_revision: constraints.data.data.revision,
      })
      setSession(conceptConfirmation.data.data)
      viewStage('world_style', true)
      setBusy(false)
      await startStageRun(quickMode ? 'all' : 'world_style', quickMode, conceptConfirmation.data.data)
    } catch (error) {
      setBusy(false)
      message.error(errorText(error))
    }
  }

  const confirmCurrentStage = async (continueToNext: boolean) => {
    if (!session || !currentStageState?.data) return
    setBusy(true)
    setStageActionError('')
    try {
      const response = await apiClient.post<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}/stages/${currentStage}/confirm`, {
        data: currentStageState.data,
        confirm: true,
        source: 'author',
        expected_revision: session.revision,
      })
      const refreshed = response.data.data
      setSession(refreshed)
      setBusy(false)
      if (!continueToNext) {
        viewStage(currentStage, true)
        focusStageHeading()
        return
      }
      const next = refreshed.stage_flow?.recommended_stage || refreshed.current_stage
      if (next && CORE_STAGES.includes(next)) {
        viewStage(next)
        const started = await startStageRun(next, false, refreshed)
        if (!started) {
          setStageActionError(`${stageLabels[currentStage] || currentStage}已确认，但${stageLabels[next] || next}尚未开始生成。你可以安全重试下一阶段。`)
          viewStage(currentStage, true)
        }
      }
    } catch (error) {
      setBusy(false)
      setStageActionError(errorText(error))
      message.error(errorText(error))
    }
  }

  const continueFromConfirmedStage = async () => {
    if (!session) return
    const target = recommendedStage && CORE_STAGES.includes(recommendedStage)
      ? recommendedStage
      : nextStage
    if (!target) return
    viewStage(target)
    const started = await startStageRun(target, false, session)
    if (!started) viewStage(currentStage, true)
  }

  const openEditor = () => {
    const data = currentStageState?.data || {}
    setEditorData(data)
    setEditorText(JSON.stringify(data, null, 2))
    setEditorOpen(true)
  }

  const updateStructuredEditor = (data: Record<string, unknown>) => {
    setEditorData(data)
    setEditorText(JSON.stringify(data, null, 2))
  }

  const saveEditor = async () => {
    if (!session) return
    try {
      const data = JSON.parse(editorText) as Record<string, unknown>
      const response = await apiClient.patch<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}/stages/${currentStage}`, {
        data,
        source: 'author',
        expected_revision: session.revision,
      })
      setSession(response.data.data)
      viewStage(currentStage, true)
      setEditorOpen(false)
      message.success('修改已保存，下游阶段已按需标记为待重新生成')
    } catch (error) {
      message.error(error instanceof SyntaxError ? '结构内容不是有效 JSON，请检查括号和引号' : errorText(error))
    }
  }

  const createProject = async () => {
    if (!session) return
    setBusy(true)
    try {
      const response = await apiClient.post<ApiResponse<{ project_id: string; warnings?: string[] }>>('/novel-creation/apply', { session_id: session.id, mode: 'auto' })
      const warnings = response.data.data.warnings || []
      if (warnings.length) message.warning(warnings.join('；'))
      else message.success('正式作品已创建，正在进入工作区')
      navigate(`/project/${response.data.data.project_id}`)
    } catch (error) {
      message.error(errorText(error))
    } finally {
      setBusy(false)
    }
  }

  const deleteDraft = async (sessionId: string) => {
    await apiClient.delete(`/novel-creation/sessions/${sessionId}`)
    if (session?.id === sessionId) {
      setSession(null)
      setSearchParams({}, { replace: true })
    }
    await loadSessions()
    message.success('未完成草稿已删除')
  }

  const resetWorkspace = () => {
    setSession(null)
    setSearchParams({}, { replace: true })
    form.resetFields()
    if (catalog?.categories[0]) applyPreset(catalog.categories[0])
  }

  if (!catalog) return <div className="creation-loading"><Spin size="large" /><Text>正在加载立项工作台...</Text></div>

  const inConceptSelection = concepts.length > 0 && !selectedConceptId
  const inWorkbench = Boolean(selectedConceptId)
  const finalData = session?.draft?.stages.final_review?.data as Record<string, unknown> | undefined
  const recommendedStageLabel = recommendedStage ? stageLabels[recommendedStage] || recommendedStage : ''
  const nextStageLabel = nextStage ? stageLabels[nextStage] || nextStage : recommendedStageLabel
  const currentBlockers = currentStageFlow?.blocked_by || []

  return (
    <div className="creation-page">
      <div className="creation-page-inner">
        <SystemNav current="creation" />
        <header className="creation-header">
          <div>
            <Title level={2}><BookOutlined /> 新书立项工作台</Title>
            <Paragraph>先比较创意，再逐步确认世界、角色与全书结构。正式作品只在最终确认时创建。</Paragraph>
          </div>
          <Space wrap>
            {session && <Tag color="processing">草稿修订 {session.revision}</Tag>}
            {saveNotice && <Tag color={saveNotice.includes('失败') ? 'warning' : 'default'}>{saveNotice}</Tag>}
            {!inWorkbench && hasModels && modelOptions.length > 1 && <Select aria-label="选择本阶段模型" loading={modelsLoading} value={selectedModel} onChange={setSelectedModel} options={modelOptions} placeholder="切换可用模型" style={{ minWidth: 260 }} />}
            {!inWorkbench && hasModels && modelOptions.length === 1 && <Tag color="success">AI 已准备好</Tag>}
            <Button icon={<SettingOutlined />} onClick={() => navigate('/settings')}>配置模型</Button>
            {session && <Button onClick={resetWorkspace}>新建立项</Button>}
          </Space>
        </header>

        {!hasModels && (
          <Alert className="creation-model-alert" type="warning" showIcon message="当前没有可用模型" description="你仍可填写并保存立项草稿。也可以先免费体验，不需要命令行或 API Key。" action={<Button type="primary" onClick={() => navigate('/getting-started')}>免费开始</Button>} />
        )}

        {!session || concepts.length === 0 ? (
          <div className="creation-intake-layout">
            <aside className="creation-taxonomy">
              <Input.Search value={presetSearch} onChange={(event) => setPresetSearch(event.target.value)} placeholder="搜索题材或主题" allowClear />
              <div className="creation-taxonomy-heading">
                <Text strong>选择题材</Text>
                <Text type="secondary" className="creation-preset-scroll-hint">左右滑动选择</Text>
              </div>
              <div className="creation-preset-list">
                {filteredPresets.map((preset) => (
                  <button key={preset.id} type="button" className={`creation-preset-item ${form.getFieldValue('preset_id') === preset.id ? 'active' : ''}`} onClick={() => applyPreset(preset)}>
                    <strong>{preset.label}</strong><span>{preset.description}</span>
                  </button>
                ))}
                <button type="button" className={`creation-preset-item ${form.getFieldValue('preset_id') === 'free' ? 'active' : ''}`} onClick={() => { form.setFieldsValue({ preset_id: 'free', genre: '自由创作', theme_id: undefined }); markFormEdited() }}>
                  <strong>自由创作</strong><span>不套用题材画像，从作者约束开始</span>
                </button>
              </div>
            </aside>

            <main className="creation-intake-main">
              <Form form={form} layout="vertical" requiredMark="optional" onValuesChange={markFormEdited}>
                <Form.Item name="preset_id" hidden><Input /></Form.Item>
                <Form.Item name="genre" hidden><Input /></Form.Item>
                <div className="creation-form-heading"><div><Title level={3}>把故事的边界说清楚</Title><Text type="secondary">题材画像会自动填入可编辑约束，任何字段都不是强制答案。</Text></div>{activePreset && <Tooltip title="恢复该题材的原始预设"><Button icon={<ReloadOutlined />} onClick={() => applyPreset(activePreset)}>恢复预设</Button></Tooltip>}</div>
                <Form.Item name="brief" label="故事梗概或最想写的画面" rules={[{ required: true, message: '至少写一句你想创作的故事' }]}>
                  <TextArea rows={6} maxLength={5000} showCount placeholder="例如：一个能看见病毒记忆的女孩，在被感染的城市里寻找失踪的母亲。她每救一个人，自己就会忘掉一段过去。" />
                </Form.Item>
                <div className="creation-form-grid">
                  <Form.Item name="theme_id" label="细分主题"><Select options={(activePreset?.themes || []).map((item) => ({ value: item.id, label: item.label }))} placeholder="选择细分主题" /></Form.Item>
                  <Form.Item name="target_audience" label="目标读者"><Select options={catalog.audiences.map((item) => ({ value: item, label: item }))} /></Form.Item>
                  <Form.Item name="platform" label="发布平台"><Select options={catalog.platforms.map((item) => ({ value: item, label: item }))} /></Form.Item>
                  <Form.Item label="目标篇幅">
                    <Radio.Group
                      optionType="button"
                      buttonStyle="solid"
                      options={catalog.length_options.map((item) => ({ label: item.label, value: item.id }))}
                      onChange={(event) => {
                        const length = catalog.length_options.find((item) => item.id === event.target.value)
                        if (length) {
                          form.setFieldsValue({ target_words: length.words, target_chapters: length.chapters })
                          markFormEdited()
                        }
                      }}
                    />
                  </Form.Item>
                </div>
                <Collapse ghost activeKey={advancedOpen ? ['advanced'] : []} onChange={(keys) => setAdvancedOpen(keys.includes('advanced'))} items={[{
                  key: 'advanced',
                  label: <Space><ExperimentOutlined />创作约束与高级设置<Badge count="均可编辑" color="var(--ant-color-primary)" /></Space>,
                  children: <>
                    <div className="creation-form-grid">
                      <Form.Item name="world_tone" label="世界观基调"><TextArea rows={3} /></Form.Item>
                      <Form.Item name="story_structure" label="剧情结构"><TextArea rows={3} /></Form.Item>
                      <Form.Item name="pacing" label="节奏控制"><TextArea rows={3} /></Form.Item>
                      <Form.Item name="writing_style" label="正文风格"><TextArea rows={3} /></Form.Item>
                    </div>
                    <div className="creation-form-grid creation-number-grid">
                      <Form.Item name="target_words" label="目标字数"><InputNumber min={10000} max={10000000} step={10000} style={{ width: '100%' }} /></Form.Item>
                      <Form.Item name="target_chapters" label="预计总章数"><InputNumber min={1} max={5000} style={{ width: '100%' }} /></Form.Item>
                    </div>
                    <Form.Item label="特殊要求" name="special_requirements" getValueFromEvent={(event) => splitLines(event.target.value)} getValueProps={(value) => ({ value: Array.isArray(value) ? value.join('\n') : '' })}><TextArea rows={3} placeholder="每行一条" /></Form.Item>
                    <Form.Item label="避雷项" name="avoid" getValueFromEvent={(event) => splitLines(event.target.value)} getValueProps={(value) => ({ value: Array.isArray(value) ? value.join('\n') : '' })}><TextArea rows={3} placeholder="每行一条" /></Form.Item>
                    <Alert type="info" showIcon message="预设内容与作者改动" description="恢复预设只会重置题材画像字段，不会清空你的故事梗概、读者、平台和篇幅。保存后再次修改已确认内容，会把依赖阶段标记为需要重新校验。" />
                  </>,
                }]} />
                <div className="creation-primary-actions">
                  <Button size="large" icon={<SaveOutlined />} loading={busy} onClick={saveIntake}>只保存草稿</Button>
                  <Button size="large" type="primary" icon={<RocketOutlined />} loading={busy} disabled={!hasModels || !selectedModel} onClick={generateConcepts}>生成三套轻量创意</Button>
                </div>
              </Form>
            </main>

            {otherSessions.length > 0 && (
              <section className="creation-resume-band">
                <Title level={4}>继续未完成立项</Title>
                <div className="creation-resume-list">
                  {otherSessions.slice(0, 4).map((item) => (
                    <Card size="small" key={item.id} onClick={() => void loadSession(item.id)} hoverable title={item.draft?.form?.brief?.slice(0, 30) || '未命名立项'} extra={<Button type="text" danger icon={<DeleteOutlined />} aria-label="删除草稿" onClick={(event) => { event.stopPropagation(); void deleteDraft(item.id) }} />}>
                      <Text type="secondary">{item.draft?.form?.genre || '自由创作'} · {item.updated_at ? new Date(item.updated_at).toLocaleString('zh-CN') : '刚刚保存'}</Text>
                    </Card>
                  ))}
                </div>
              </section>
            )}
          </div>
        ) : inConceptSelection ? (
          <main className="creation-concepts-main">
            <div className="creation-section-heading"><div><Title level={3}>先选故事发动机</Title><Paragraph>这里只展示足够做方向判断的内容。选中后再生成完整角色、世界和全书规划。</Paragraph></div><Button icon={<ReloadOutlined />} onClick={generateConcepts} loading={busy}>重新生成三案</Button></div>
            <div className="creation-concept-grid">
              {concepts.map((concept, index) => (
                <Card key={concept.id} className="creation-concept-card" title={<Space><Badge count={index + 1} color="var(--ant-color-primary)" /><span>{concept.title}</span></Space>} extra={<Tag>{concept.coverage?.score || 0}% 覆盖</Tag>}>
                  <Text type="secondary">{concept.subtitle}</Text>
                  <Paragraph className="creation-logline">{concept.logline}</Paragraph>
                  <Descriptions column={1} size="small">
                    <Descriptions.Item label="主角">{concept.protagonist_seed?.name} · {concept.protagonist_seed?.goal}</Descriptions.Item>
                    <Descriptions.Item label="世界钩子">{concept.world_hook}</Descriptions.Item>
                    <Descriptions.Item label="核心冲突">{concept.core_conflict}</Descriptions.Item>
                    <Descriptions.Item label="故事发动机">{concept.story_engine}</Descriptions.Item>
                    <Descriptions.Item label="开篇钩子">{concept.opening_hook}</Descriptions.Item>
                  </Descriptions>
                  <Space className="creation-differentiators" wrap>{concept.differentiators?.map((item) => <Tag color="blue" key={item}>{item}</Tag>)}</Space>
                  {concept.risks?.length > 0 && <Alert className="creation-risk" type="warning" message={concept.risks.join('；')} />}
                  <div className="creation-concept-actions">
                    <Button icon={<PlayCircleOutlined />} onClick={() => void chooseConcept(concept.id, true)} disabled={busy}>快速生成到最终审阅</Button>
                    <Button type="primary" onClick={() => void chooseConcept(concept.id, false)} disabled={busy}>进入完整向导</Button>
                  </div>
                </Card>
              ))}
            </div>
          </main>
        ) : inWorkbench ? (
          <main className="creation-workbench">
            <aside className="creation-stage-nav">
              <Title level={4}>立项进度</Title>
              <FlowNavigator
                direction="vertical"
                current={Math.max(0, CORE_STAGES.indexOf(currentStage))}
                onChange={(index) => {
                  const stage = CORE_STAGES[index]
                  const canView = session?.stage_flow?.items?.[stage]?.can_view
                    ?? Boolean(session?.draft?.stages?.[stage]?.data || stage === currentStage)
                  if (canView) viewStage(stage)
                }}
                items={CORE_STAGES.map((stage) => {
                  const state = session?.draft?.stages[stage]
                  const flow = session?.stage_flow?.items?.[stage]
                  const canView = flow?.can_view ?? Boolean(state?.data || stage === currentStage)
                  return {
                    title: stageLabels[stage] || stage,
                    disabled: !canView,
                    status: state?.status === 'confirmed'
                      ? 'finish'
                      : state?.status === 'stale'
                        ? 'error'
                        : stage === currentStage || state?.status === 'generated'
                          ? 'process'
                          : 'wait',
                    description: (
                      <Space direction="vertical" size={2}>
                        <Tag color={stageTone(state?.status)}>{stageStatusLabel(state?.status)}</Tag>
                        {!canView && flow?.blocked_by?.[0] && <Text type="secondary">先确认{flow.blocked_by[0].label}</Text>}
                      </Space>
                    ),
                  }
                })}
              />
              <Alert type="info" showIcon message={session?.draft?.quick_mode ? '快速模式' : '完整向导'} description="所有内容仍在立项草稿中，最终确认前不会创建正式作品。" />
            </aside>
            <section className="creation-stage-main">
              <div className="creation-section-heading">
                <div ref={stageHeadingRef} tabIndex={-1} className="creation-stage-heading-focus">
                  <Title level={3}>{stageLabels[currentStage] || currentStage}</Title>
                  <Space wrap>
                    <Tag color={stageTone(currentStageState?.status)}>{stageStatusLabel(currentStageState?.status)}</Tag>
                    {currentStage === attentionStage && currentStageState?.status === 'generated' && <Tag className="creation-attention-tag">需要你的确认</Tag>}
                    {currentStageState?.stale_reason && <Text type="warning">{currentStageState.stale_reason}</Text>}
                  </Space>
                </div>
                <Space wrap>
                  <Select aria-label="选择当前阶段模型" value={selectedModel} onChange={setSelectedModel} options={modelOptions} style={{ minWidth: 250 }} />
                  <Button icon={<ReloadOutlined />} onClick={() => void startStageRun(currentStage)} disabled={busy || currentStageFlow?.can_generate === false}>重新生成</Button>
                  <Button icon={<EditOutlined />} onClick={openEditor} disabled={!currentStageState?.data || busy}>编辑阶段内容</Button>
                </Space>
              </div>
              <StageFeedback
                currentStage={currentStage}
                status={currentStageState?.status}
                hasData={Boolean(currentStageState?.data)}
                staleReason={currentStageState?.stale_reason}
                blockers={currentBlockers}
                error={stageActionError}
                recommendedStageLabel={recommendedStageLabel}
                canRetryNext={Boolean(recommendedStage && recommendedStage !== currentStage)}
                onViewStage={viewStage}
                onRetryNext={() => void continueFromConfirmedStage()}
              />
              {currentStageState?.source === 'contract_fallback' && (
                <Alert
                  type="warning"
                  showIcon
                  message="本阶段已采用安全结构继续"
                  description="模型回复为空或格式不可用。内容没有丢失，你可以直接检查、编辑，或选择其他模型重新生成本阶段。"
                />
              )}
              <StagePreview stage={currentStage} data={currentStageState?.data} />
              <StageActionBar
                currentStage={currentStage}
                status={currentStageState?.status}
                hasData={Boolean(currentStageState?.data)}
                busy={busy}
                createdProjectId={session.created_project_id}
                finalReady={Boolean(finalData?.ready)}
                recommendedStage={recommendedStage}
                recommendedStageLabel={recommendedStageLabel}
                nextStageLabel={nextStageLabel}
                canGenerate={Boolean(currentStageFlow?.can_generate)}
                currentStageLabel={stageLabels[currentStage] || currentStage}
                onOpenProject={() => {
                  if (session.created_project_id) navigate(`/project/${session.created_project_id}`)
                }}
                onCreateProject={() => void createProject()}
                onConfirmOnly={() => void confirmCurrentStage(false)}
                onConfirmAndContinue={() => void confirmCurrentStage(true)}
                onContinue={() => void continueFromConfirmedStage()}
                onGenerate={() => void startStageRun(currentStage)}
              />
            </section>
          </main>
        ) : null}

        {busy && (
          <div className="creation-run-bar" aria-live="polite">
            <CloudSyncOutlined spin />
            <div className="creation-run-detail">
              <Space size={6} wrap>
                <Text strong>{runMessage || '正在处理立项任务...'}</Text>
                <Tag color={runConnection === 'connected' ? 'processing' : 'warning'}>{runConnection === 'connected' ? '运行中' : '正在重新连接'}</Tag>
                {activeRun?.model_source && <Tag>{activeRun.model_source}</Tag>}
                {activeRun?.input_revision != null && <Tag>基于草稿 v{activeRun.input_revision}</Tag>}
              </Space>
              {activeRun?.stage === 'all' && runProgress > 0
                ? <Progress percent={runProgress} status="active" showInfo />
                : <div className="creation-run-indeterminate"><Spin size="small" /><Text type="secondary">模型正在推进；无法准确估算时不显示虚假百分比</Text></div>}
              {editedDuringRunRef.current && <Text type="warning">你刚才的修改会保存为下一版，不会改变当前这次生成。</Text>}
            </div>
          </div>
        )}

        {resultRevisionNotice && !busy && (
          <Alert
            className="creation-result-revision"
            type="info"
            showIcon
            message={resultRevisionNotice}
            action={editedDuringRunRef.current ? (
              <Space wrap>
                <Button onClick={() => { editedDuringRunRef.current = false; setResultRevisionNotice('') }}>接受本次结果</Button>
                <Button type="primary" onClick={() => activeRun?.stage === 'concepts' ? void generateConcepts() : void startStageRun(activeRun?.stage || currentStage)}>按最新版重新生成</Button>
              </Space>
            ) : undefined}
          />
        )}

        {session?.last_error && !busy && (() => {
          const failedStage = session.last_error.failed_stage
          const retryStage = failedStage && [...CORE_STAGES, 'concepts'].includes(failedStage) ? failedStage : currentStage
          const retryLabel = session.last_error.failed_stage_label || stageLabels[retryStage] || retryStage
          return (
            <Alert
              className="creation-error-band"
              type="error"
              showIcon
              message={session.last_error.message || '阶段运行失败'}
              description={session.last_error.next_action}
              action={<Button onClick={() => retryStage === 'concepts' ? void generateConcepts() : void startStageRun(retryStage)}>重试“{retryLabel}”</Button>}
            />
          )
        })()}
      </div>

      <Modal title={`编辑：${stageLabels[currentStage] || currentStage}`} open={editorOpen} onCancel={() => setEditorOpen(false)} onOk={saveEditor} okText="保存修改" width={960}>
        <Alert type="info" showIcon message="直接修改需要调整的字段" description="保存后，下游已经确认的阶段会标记为需要重新校验。列表和复杂关系可以展开逐项编辑。" />
        <div className="creation-structured-editor">
          <StructuredStageEditor data={editorData} onChange={updateStructuredEditor} />
        </div>
        <Collapse
          className="creation-advanced-editor"
          items={[{
            key: 'json',
            label: '高级编辑：JSON 原文',
            children: <><Paragraph type="secondary">仅在需要批量修改结构时使用；保存时会校验格式。</Paragraph><TextArea aria-label="阶段 JSON 原文" className="creation-json-editor" value={editorText} onChange={(event) => setEditorText(event.target.value)} rows={24} spellCheck={false} /></>,
          }]}
        />
      </Modal>

    </div>
  )
}

export default NovelCreationWizardPage
