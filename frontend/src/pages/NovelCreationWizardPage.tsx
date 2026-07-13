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
  Steps,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  ArrowLeftOutlined,
  BookOutlined,
  CheckCircleOutlined,
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
import { apiClient } from '../api/client'
import { useModelOptions } from '../hooks/useModelOptions'
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
  updated_at?: string
  last_error?: { failure_class?: string; message?: string; next_action?: string }
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
  stage: string
  status: string
  current_message?: string
  failure_class?: string
  next_action?: string
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

function StagePreview({ stage, data }: { stage: string; data?: Record<string, unknown> | null }) {
  if (!data) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本阶段尚未生成" />

  if (stage === 'world_style') {
    const world = Array.isArray(data.worldbuilding) ? data.worldbuilding as Array<Record<string, unknown>> : []
    return (
      <div className="creation-stage-preview">
        <Descriptions column={2} size="small" bordered>
          <Descriptions.Item label="世界基调">{String(data.world_tone || '')}</Descriptions.Item>
          <Descriptions.Item label="正文风格">{String(data.writing_style || '')}</Descriptions.Item>
          <Descriptions.Item label="结构">{String(data.story_structure || '')}</Descriptions.Item>
          <Descriptions.Item label="节奏">{String(data.pacing || '')}</Descriptions.Item>
        </Descriptions>
        <div className="creation-item-grid">
          {world.map((item, index) => (
            <Card key={`${String(item.title)}-${index}`} size="small" title={String(item.title || `设定 ${index + 1}`)} extra={<Tag>{String(item.dimension || 'culture')}</Tag>}>
              <Paragraph ellipsis={{ rows: 4, expandable: true, symbol: '展开' }}>{String(item.content || item.description || '')}</Paragraph>
            </Card>
          ))}
        </div>
      </div>
    )
  }

  if (stage === 'characters') {
    const characters = Array.isArray(data.characters) ? data.characters as Array<Record<string, unknown>> : []
    return (
      <div className="creation-item-grid creation-character-grid">
        {characters.map((item, index) => {
          const profile = (item.profile || {}) as Record<string, unknown>
          return (
            <Card key={`${String(item.name)}-${index}`} size="small" title={String(item.name || `角色 ${index + 1}`)} extra={<Tag>{String(item.role_type || 'supporting')}</Tag>}>
              <Paragraph>{String(item.background || item.personality || '')}</Paragraph>
              <Descriptions column={1} size="small">
                <Descriptions.Item label="当前目标">{String(item.goal || item.current_goal || '')}</Descriptions.Item>
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
    const entries = Array.isArray(data.entries) ? data.entries as Array<Record<string, unknown>> : []
    const relations = Array.isArray(data.relations) ? data.relations as Array<Record<string, unknown>> : []
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
    const volumes = Array.isArray(data.volumes) ? data.volumes as Array<Record<string, unknown>> : []
    return (
      <div className="creation-stage-preview">
        <Alert type="info" showIcon message={String(data.core_conflict || '')} description={String(data.story_overview || '')} />
        <Timeline className="creation-volume-timeline" items={volumes.map((item) => ({
          children: <div><Text strong>{String(item.title || '')}</Text><Tag style={{ marginLeft: 8 }}>{String(item.start_chapter || '?')} - {String(item.end_chapter || '?')} 章</Tag><Paragraph>{String(item.summary || '')}</Paragraph></div>,
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
              return <div className="creation-section-row" key={`${String(section.client_id)}-${index}`}><Badge count={index + 1} color="#3f6f67" /><div><Text strong>{String(section.title)}</Text><Paragraph type="secondary">{String(metadata.purpose || section.summary || '')}</Paragraph><Space wrap size={4}><Tag>{String(metadata.location || '地点待定')}</Tag><Tag>{String(metadata.pov_character || '视角待定')}</Tag><Tag>{String(metadata.exit_state || '状态待定')}</Tag></Space></div></div>
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
  const [presetSearch, setPresetSearch] = useState('')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const watchingRunRef = useRef<string | null>(null)

  const requestedSessionId = searchParams.get('session') || undefined
  const requestedRunId = searchParams.get('run')
  const requestedModel = searchParams.get('model') || undefined

  const watchedPresetId = Form.useWatch('preset_id', form)
  const activePreset = useMemo(() => catalog?.categories.find((item) => item.id === watchedPresetId), [catalog, watchedPresetId])
  const concepts = session?.draft?.concepts || []
  const selectedConceptId = session?.draft?.selected_concept_id
  const currentStage = session?.current_stage && CORE_STAGES.includes(session.current_stage) ? session.current_stage : 'world_style'
  const currentStageState = session?.draft?.stages?.[currentStage]
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
    const response = await apiClient.get<ApiResponse<CreationSession>>(`/novel-creation/sessions/${sessionId}`)
    const loaded = response.data.data
    setSession(loaded)
    if (loaded.draft?.form) form.setFieldsValue(loaded.draft.form)
    setSearchParams((current) => {
      if (current.get('session') === loaded.id) return current
      const next = new URLSearchParams(current)
      next.set('session', loaded.id)
      return next
    }, { replace: true })
    return loaded
  }, [form, setSearchParams])

  useEffect(() => {
    const initialize = async () => {
      try {
        const response = await apiClient.get<ApiResponse<PresetCatalog>>('/novel-creation/presets')
        setCatalog(response.data.data)
        const first = response.data.data.categories[0]
        form.setFieldsValue({
          brief: '', preset_id: first?.id || 'free', theme_id: first?.themes[0]?.id,
          genre: first?.label || '自由创作', target_audience: '成年大众', platform: '暂不确定',
          target_words: 600000, target_chapters: 240,
          world_tone: first?.defaults.world_tone || '', story_structure: first?.defaults.story_structure || '',
          pacing: first?.defaults.pacing || '', writing_style: first?.defaults.writing_style || '',
          special_requirements: first?.defaults.special_requirements || [], avoid: first?.defaults.avoid || [],
        })
        await loadSessions()
        if (requestedSessionId) await loadSession(requestedSessionId)
      } catch (error) {
        message.error(errorText(error))
      }
    }
    void initialize()
  }, [form, loadSession, loadSessions, requestedSessionId])

  useEffect(() => {
    if (defaultModel && !selectedModel) setSelectedModel(defaultModel)
  }, [defaultModel, selectedModel])

  useEffect(() => {
    if (requestedModel) setSelectedModel(requestedModel)
  }, [requestedModel])

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
  }

  const persistIntake = async () => {
    const values = await form.validateFields()
    if (session && concepts.length === 0) {
      const response = await apiClient.patch<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}`, { form: values })
      setSession(response.data.data)
      return response.data.data
    }
    const response = await apiClient.post<ApiResponse<{ session_id: string; session: CreationSession }>>('/novel-creation/start', {
      mode: 'internal_llm',
      user_brief: values.brief,
      ...values,
    })
    setSession(response.data.data.session)
    setSearchParams({ session: response.data.data.session_id }, { replace: true })
    await loadSessions()
    return response.data.data.session
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
    try {
      const saved = await persistIntake()
      const response = await apiClient.post<ApiResponse<{ run: StageRun }>>(`/novel-creation/sessions/${saved.id}/runs`, {
        stage: 'concepts',
        model: selectedModel,
        use_model: true,
        operation: 'generate_concepts',
      })
      setSearchParams({ session: saved.id, run: response.data.data.run.id, ...(selectedModel ? { model: selectedModel } : {}) }, { replace: true })
      watchRun(response.data.data.run.id)
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
    const source = new EventSource(`/api/v1/novel-creation/runs/${runId}/stream`)
    const handleEvent = (event: MessageEvent) => {
      try {
        const payload = JSON.parse(event.data) as { message?: string; event_type?: string; payload?: { stage?: string } }
        if (payload.message) setRunMessage(payload.message)
        const stageIndex = payload.payload?.stage ? CORE_STAGES.indexOf(payload.payload.stage) : -1
        if (stageIndex >= 0) {
          const completed = payload.event_type === 'stage_completed'
          setRunProgress(Math.round(((stageIndex + (completed ? 1 : 0.25)) / CORE_STAGES.length) * 100))
        }
      } catch { /* keep the last readable status */ }
    }
    source.addEventListener('started', handleEvent as EventListener)
    source.addEventListener('stage_progress', handleEvent as EventListener)
    source.addEventListener('stage_completed', handleEvent as EventListener)
    source.addEventListener('completed', handleEvent as EventListener)
    source.addEventListener('failed', handleEvent as EventListener)
    source.addEventListener('done', (event) => {
      source.close()
      watchingRunRef.current = null
      try {
        const finished = JSON.parse((event as MessageEvent).data) as StageRun
        if (finished.status === 'failed') {
          message.error(finished.current_message || '阶段生成失败')
        } else {
          message.success('阶段结果已保存到立项草稿')
        }
      } catch { /* the session refresh below is authoritative */ }
      if (session) void loadSession(session.id)
      setBusy(false)
      setRunMessage('')
      setRunProgress(100)
    })
    source.onerror = () => {
      source.close()
      watchingRunRef.current = null
      if (session) void loadSession(session.id)
      setBusy(false)
      setRunMessage('')
      setRunProgress(0)
    }
  }, [loadSession, session])

  useEffect(() => {
    const activeRun = requestedRunId
      ? session?.runs?.find((run) => run.id === requestedRunId && run.status === 'running')
      : session?.runs?.find((run) => run.status === 'running')
    if (!activeRun) return
    setRunMessage(activeRun.current_message || '正在恢复立项任务...')
    watchRun(activeRun.id)
  }, [requestedRunId, session?.id, session?.runs, watchRun])

  const startStageRun = async (stage: string, autoConfirm = false) => {
    if (!session || !selectedModel) return
    setBusy(true)
    setRunProgress(5)
    setRunMessage(`正在生成${stage === 'all' ? '完整立项档案' : stageLabels[stage] || stage}...`)
    try {
      const response = await apiClient.post<ApiResponse<{ run: StageRun }>>(`/novel-creation/sessions/${session.id}/runs`, {
        stage,
        model: selectedModel,
        use_model: true,
        auto_confirm: autoConfirm,
      })
      watchRun(response.data.data.run.id)
    } catch (error) {
      setBusy(false)
      setRunMessage('')
      setRunProgress(0)
      message.error(errorText(error))
    }
  }

  const chooseConcept = async (conceptId: string, quickMode: boolean) => {
    if (!session) return
    setBusy(true)
    try {
      await apiClient.patch(`/novel-creation/sessions/${session.id}`, { selected_concept_id: conceptId, quick_mode: quickMode })
      await apiClient.post(`/novel-creation/sessions/${session.id}/stages/constraints/confirm`, { data: session.draft?.form, confirm: true, source: 'author' })
      await apiClient.post(`/novel-creation/sessions/${session.id}/stages/concepts/confirm`, { data: { options: concepts, selected_concept_id: conceptId }, confirm: true, source: 'author' })
      await loadSession(session.id)
      setBusy(false)
      await startStageRun(quickMode ? 'all' : 'world_style', quickMode)
    } catch (error) {
      setBusy(false)
      message.error(errorText(error))
    }
  }

  const confirmCurrentStage = async () => {
    if (!session || !currentStageState?.data) return
    setBusy(true)
    try {
      await apiClient.post(`/novel-creation/sessions/${session.id}/stages/${currentStage}/confirm`, { data: currentStageState.data, confirm: true, source: 'author' })
      const refreshed = await loadSession(session.id)
      const next = refreshed.current_stage
      setBusy(false)
      if (next && next !== 'final_review') await startStageRun(next)
      else if (next === 'final_review' && !refreshed.draft?.stages.final_review?.data) await startStageRun('final_review')
    } catch (error) {
      setBusy(false)
      message.error(errorText(error))
    }
  }

  const openEditor = () => {
    setEditorText(JSON.stringify(currentStageState?.data || {}, null, 2))
    setEditorOpen(true)
  }

  const saveEditor = async () => {
    if (!session) return
    try {
      const data = JSON.parse(editorText) as Record<string, unknown>
      await apiClient.post(`/novel-creation/sessions/${session.id}/stages/${currentStage}/confirm`, { data, confirm: false, source: 'author' })
      await loadSession(session.id)
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

  return (
    <div className="creation-page">
      <div className="creation-page-inner">
        <SystemNav />
        <header className="creation-header">
          <div>
            <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/dashboard')}>返回作品管理</Button>
            <Title level={2}><BookOutlined /> 新书立项工作台</Title>
            <Paragraph>先比较创意，再逐步确认世界、角色与全书结构。正式作品只在最终确认时创建。</Paragraph>
          </div>
          <Space wrap>
            {session && <Tag color="processing">草稿修订 {session.revision}</Tag>}
            <Select aria-label="选择本阶段模型" loading={modelsLoading} value={selectedModel} onChange={setSelectedModel} options={modelOptions} placeholder="选择本阶段模型" style={{ minWidth: 260 }} />
            <Button icon={<SettingOutlined />} onClick={() => navigate('/settings')}>配置模型</Button>
            {session && <Button onClick={resetWorkspace}>新建立项</Button>}
          </Space>
        </header>

        {!hasModels && (
          <Alert className="creation-model-alert" type="warning" showIcon message="当前没有可用模型" description="你仍可填写并保存立项草稿。需要生成创意时，请先进入系统设置，选择 API 或本机 CLI，按页面指引完成安装、凭据填写和连接测试。" action={<Button type="primary" onClick={() => navigate('/settings')}>打开系统设置</Button>} />
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
                <button type="button" className={`creation-preset-item ${form.getFieldValue('preset_id') === 'free' ? 'active' : ''}`} onClick={() => form.setFieldsValue({ preset_id: 'free', genre: '自由创作', theme_id: undefined })}>
                  <strong>自由创作</strong><span>不套用题材画像，从作者约束开始</span>
                </button>
              </div>
            </aside>

            <main className="creation-intake-main">
              <Form form={form} layout="vertical" requiredMark="optional">
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
                        if (length) form.setFieldsValue({ target_words: length.words, target_chapters: length.chapters })
                      }}
                    />
                  </Form.Item>
                </div>
                <Collapse ghost activeKey={advancedOpen ? ['advanced'] : []} onChange={(keys) => setAdvancedOpen(keys.includes('advanced'))} items={[{
                  key: 'advanced',
                  label: <Space><ExperimentOutlined />创作约束与高级设置<Badge count="均可编辑" color="#3f6f67" /></Space>,
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
                  <Button size="large" type="primary" icon={<RocketOutlined />} loading={busy} onClick={generateConcepts}>生成三套轻量创意</Button>
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
                <Card key={concept.id} className="creation-concept-card" title={<Space><Badge count={index + 1} color="#3f6f67" /><span>{concept.title}</span></Space>} extra={<Tag>{concept.coverage?.score || 0}% 覆盖</Tag>}>
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
              <Steps direction="vertical" current={Math.max(0, CORE_STAGES.indexOf(currentStage))} items={CORE_STAGES.map((stage) => ({
                title: stageLabels[stage] || stage,
                status: session?.draft?.stages[stage]?.status === 'confirmed' ? 'finish' : session?.draft?.stages[stage]?.status === 'stale' ? 'error' : stage === currentStage ? 'process' : 'wait',
                description: <Tag color={stageTone(session?.draft?.stages[stage]?.status)}>{session?.draft?.stages[stage]?.status || 'pending'}</Tag>,
              }))} />
              <Alert type="info" showIcon message={session?.draft?.quick_mode ? '快速模式' : '完整向导'} description="所有内容仍在立项草稿中，最终确认前不会创建正式作品。" />
            </aside>
            <section className="creation-stage-main">
              <div className="creation-section-heading">
                <div><Title level={3}>{stageLabels[currentStage] || currentStage}</Title><Space><Tag color={stageTone(currentStageState?.status)}>{currentStageState?.status || 'pending'}</Tag>{currentStageState?.stale_reason && <Text type="warning">{currentStageState.stale_reason}</Text>}</Space></div>
                <Space wrap>
                  <Select aria-label="选择当前阶段模型" value={selectedModel} onChange={setSelectedModel} options={modelOptions} style={{ minWidth: 250 }} />
                  <Button icon={<ReloadOutlined />} onClick={() => void startStageRun(currentStage)} disabled={busy}>重新生成</Button>
                  <Button icon={<EditOutlined />} onClick={openEditor} disabled={!currentStageState?.data || busy}>编辑阶段内容</Button>
                </Space>
              </div>
              <StagePreview stage={currentStage} data={currentStageState?.data} />
              <div className="creation-stage-actions">
                {currentStage === 'final_review' ? (
                  session.created_project_id ? (
                    <Button size="large" type="primary" icon={<BookOutlined />} onClick={() => navigate(`/project/${session.created_project_id}`)}>进入已创建作品</Button>
                  ) : (
                    <Button size="large" type="primary" icon={<CheckCircleOutlined />} disabled={!finalData?.ready || busy} loading={busy} onClick={createProject}>确认并创建正式作品</Button>
                  )
                ) : (
                  <Button size="large" type="primary" icon={<CheckCircleOutlined />} disabled={!currentStageState?.data || busy} loading={busy} onClick={confirmCurrentStage}>确认本阶段并继续</Button>
                )}
              </div>
            </section>
          </main>
        ) : null}

        {busy && (
          <div className="creation-run-bar" aria-live="polite">
            <CloudSyncOutlined spin />
            <div><Text strong>{runMessage || '正在处理立项任务...'}</Text><Progress percent={runProgress} status="active" showInfo={false} /></div>
          </div>
        )}

        {session?.last_error && !busy && (
          <Alert className="creation-error-band" type="error" showIcon message={session.last_error.message || '阶段运行失败'} description={session.last_error.next_action} action={<Button onClick={() => void startStageRun(currentStage)}>重试本阶段</Button>} />
        )}
      </div>

      <Modal title={`编辑：${stageLabels[currentStage] || currentStage}`} open={editorOpen} onCancel={() => setEditorOpen(false)} onOk={saveEditor} okText="保存修改" width={900}>
        <Alert type="info" showIcon message="这里显示完整结构" description="普通作者可以只改文字值，不要删除字段名。保存后，依赖这个阶段的后续内容会自动标记为需要重新生成。" />
        <TextArea className="creation-json-editor" value={editorText} onChange={(event) => setEditorText(event.target.value)} rows={24} spellCheck={false} />
      </Modal>

    </div>
  )
}

export default NovelCreationWizardPage
