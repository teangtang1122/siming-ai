import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Alert,
  Badge,
  Button,
  Card,
  Collapse,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  BookOutlined,
  CompassOutlined,
  DeleteOutlined,
  EditOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  LockOutlined,
  RobotOutlined,
  ReloadOutlined,
  RocketOutlined,
  SaveOutlined,
  SettingOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import SystemNav from '../components/SystemNav'
import {
  FlowNavigator,
} from '../components/interaction'
import { StageActionBar, StageFeedback } from '../components/novel-creation'
import { StructuredStageEditor } from '../components/novel-creation/StructuredStageEditor'
import { apiClient } from '../api/client'
import { useModelOptions } from '../hooks/useModelOptions'
import {
  startNovelCreationConceptRun,
  startNovelCreationSession,
  workbenchUrl,
} from '../services/novelCreationAgent'
import { RunStatusPanels } from '../features/novelCreation/RunStatusPanels'
import { StagePreview } from '../features/novelCreation/StageContent'
import { useNovelCreationRun } from '../features/novelCreation/useNovelCreationRun'
import {
  CORE_STAGES,
  errorText,
  splitLines,
  stageStatusLabel,
  stageTone,
  type ApiResponse,
  type CreationFormValues,
  type CreationPath,
  type CreationSession,
  type GenrePreset,
  type PresetCatalog,
  type StageRun,
} from '../features/novelCreation/types'
import './NovelCreationWizardPage.css'

const { Paragraph, Text, Title } = Typography
const { TextArea } = Input

function NovelCreationWizardPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [form] = Form.useForm<CreationFormValues>()
  const { modelOptions, defaultModel, hasModels, loading: modelsLoading } = useModelOptions()
  const [catalog, setCatalog] = useState<PresetCatalog | null>(null)
  const [sessions, setSessions] = useState<CreationSession[]>([])
  const [session, setSession] = useState<CreationSession | null>(null)
  const [creationPath, setCreationPath] = useState<CreationPath | null>(null)
  const [selectedModel, setSelectedModel] = useState<string>()
  const [busy, setBusy] = useState(false)
  const [runMessage, setRunMessage] = useState('')
  const [runProgress, setRunProgress] = useState(0)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editorText, setEditorText] = useState('')
  const [editorData, setEditorData] = useState<Record<string, unknown>>({})
  const [editorStage, setEditorStage] = useState('world_style')
  const [presetSearch, setPresetSearch] = useState('')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [formEditTick, setFormEditTick] = useState(0)
  const [saveNotice, setSaveNotice] = useState('')
  const [runConnection, setRunConnection] = useState<'connected' | 'reconnecting'>('connected')
  const [resultRevisionNotice, setResultRevisionNotice] = useState('')
  const [stageActionError, setStageActionError] = useState('')
  const [refineOpen, setRefineOpen] = useState(false)
  const [refineTarget, setRefineTarget] = useState('concepts')
  const [refineInstruction, setRefineInstruction] = useState('')
  const stageHeadingRef = useRef<HTMLDivElement | null>(null)
  const defaultsAppliedRef = useRef(false)
  const loadRequestRef = useRef(0)
  const saveRequestRef = useRef(0)
  const loadedSessionIdRef = useRef<string | null>(null)
  const hydratingFormRef = useRef(false)
  const formDirtyRef = useRef(false)
  const editTickRef = useRef(0)
  const editedDuringRunRef = useRef(false)
  const editorOriginalTextRef = useRef('')

  const requestedSessionId = searchParams.get('session') || undefined
  const requestedRunId = searchParams.get('run')
  const requestedModel = searchParams.get('model') || undefined
  const requestedStage = searchParams.get('stage') || undefined
  const requestedConversationId = searchParams.get('conversation') || undefined
  const assistantReturnUrl = requestedConversationId
    ? `/gui?creationSession=${requestedSessionId || session?.id || ''}&conversation=${requestedConversationId}`
    : `/gui?creationSession=${requestedSessionId || session?.id || ''}`

  const watchedPresetId = Form.useWatch('preset_id', form)
  const activePreset = useMemo(() => catalog?.categories.find((item) => item.id === watchedPresetId), [catalog, watchedPresetId])
  const concepts = session?.draft?.concepts || []
  const selectedConceptId = session?.draft?.selected_concept_id
  const attentionStage = session?.stage_flow?.attention_stage
  const currentStage = (
    requestedStage
    && CORE_STAGES.includes(requestedStage)
    && (
      session?.stage_flow?.items?.[requestedStage]?.can_view
      || session?.stage_flow?.items?.[requestedStage]?.can_generate
    )
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
    setCreationPath(loaded.draft?.creation_mode || 'explore')
    const switchingSession = loadedSessionIdRef.current !== loaded.id
    if (loaded.draft?.form && (switchingSession || !formDirtyRef.current)) {
      hydratingFormRef.current = true
      form.setFieldsValue({
        ...loaded.draft.form,
        author_outline: loaded.draft.author_outline || '',
        locked_requirements: loaded.draft.locked_requirements || [],
      })
      hydratingFormRef.current = false
      formDirtyRef.current = false
    }
    loadedSessionIdRef.current = loaded.id
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.set('session', loaded.id)
      const existingStage = next.get('stage')
      const flow = loaded.stage_flow
      const targetStage = existingStage && (
        flow?.items?.[existingStage]?.can_view
        || flow?.items?.[existingStage]?.can_generate
      )
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

  const invalidateSessionLoads = useCallback(() => {
    loadRequestRef.current += 1
  }, [])

  const {
    activeRun,
    setActiveRun,
    cancellingRun,
    pausingRun,
    cancelActiveRun,
    pauseActiveRun,
    resumeActiveRun,
    watchRun,
    clearRunState,
  } = useNovelCreationRun({
    session,
    requestedRunId,
    loadSession,
    invalidateSessionLoads,
    focusStageHeading,
    editedDuringRunRef,
    setBusy,
    setRunMessage,
    setRunProgress,
    setRunConnection,
    setResultRevisionNotice,
  })

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
    if (activeRun && ['queued', 'running'].includes(activeRun.status)) editedDuringRunRef.current = true
  }, [activeRun])

  useEffect(() => {
    if (!session || !formDirtyRef.current) return
    const capturedTick = formEditTick
    const requestId = ++saveRequestRef.current
    const timer = window.setTimeout(async () => {
      const values = form.getFieldsValue(true)
      const {
        author_brief: authorBrief = values.brief,
        author_outline: authorOutline = '',
        locked_requirements: lockedRequirements = [],
        ...formValues
      } = values
      const creationMode = creationPath || session.draft?.creation_mode || 'author_led'
      try {
        const response = await apiClient.patch<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}`, {
          form: formValues,
          creation_mode: creationMode,
          author_brief: creationMode === 'author_led' ? authorBrief : '',
          author_outline: creationMode === 'author_led' ? authorOutline : '',
          locked_requirements: creationMode === 'author_led' ? lockedRequirements : [],
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
  }, [creationPath, form, formEditTick, session])

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
    const {
      author_brief: authorBrief = values.brief,
      author_outline: authorOutline = '',
      locked_requirements: lockedRequirements = [],
      ...formValues
    } = values
    const creationMode = creationPath || session?.draft?.creation_mode || 'author_led'
    ++saveRequestRef.current
    if (session) {
      const save = async (expectedRevision: number) => apiClient.patch<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}`, {
        form: formValues,
        creation_mode: creationMode,
        author_brief: creationMode === 'author_led' ? authorBrief : '',
        author_outline: creationMode === 'author_led' ? authorOutline : '',
        locked_requirements: creationMode === 'author_led' ? lockedRequirements : [],
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
      form: formValues,
      creationMode,
      authorBrief: creationMode === 'author_led' ? authorBrief : '',
      authorOutline: creationMode === 'author_led' ? authorOutline : '',
      lockedRequirements: creationMode === 'author_led' ? lockedRequirements : [],
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

  const generateConcepts = async (
    operation: 'generate' | 'regenerate' | 'refine' = concepts.length ? 'regenerate' : 'generate',
    instruction?: string,
  ) => {
    if (!hasModels || !selectedModel) {
      message.warning('还没有可用模型。先到系统设置配置 API 或本机 CLI，并完成连接测试。')
      return
    }
    setBusy(true)
    const authorLed = (creationPath || session?.draft?.creation_mode) === 'author_led'
    setRunMessage(operation === 'refine'
      ? '正在按你的要求调整当前方案...'
      : authorLed ? '正在忠实整理作者方案...' : '正在理解创作约束并生成一套创意方向...')
    setRunProgress(0)
    setResultRevisionNotice('')
    editedDuringRunRef.current = false
    try {
      const saved = await persistIntake()
      const run = await startNovelCreationConceptRun(saved.id, selectedModel, saved.revision, operation, instruction)
      setActiveRun(run)
      const query = workbenchUrl(saved.id, run.id, selectedModel).split('?')[1] || ''
      setSearchParams(new URLSearchParams(query), { replace: true })
      watchRun(run.id, saved.id)
    } catch (error) {
      setBusy(false)
      setRunMessage('')
      message.error(errorText(error))
    }
  }

  const startStageRun = async (
    stage: string,
    autoConfirm = false,
    runSession: CreationSession | null = session,
    operation: 'generate' | 'regenerate' | 'refine' = 'generate',
    instruction?: string,
  ) => {
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
        operation,
        ...(instruction ? { instruction } : {}),
        expected_revision: runSession.revision,
      })
      const startedRun = {
        ...response.data.data.run,
        session_id: response.data.data.run.session_id || runSession.id,
      }
      setActiveRun(startedRun)
      setSearchParams((current) => {
        const next = new URLSearchParams(current)
        next.set('session', runSession.id)
        next.set('run', startedRun.id)
        if (selectedModel) next.set('model', selectedModel)
        if (stage !== 'all') next.set('stage', stage)
        return next
      }, { replace: true })
      watchRun(startedRun.id, runSession.id)
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

  const confirmConceptOnly = async (conceptId: string) => {
    if (!session) return
    setBusy(true)
    try {
      const selection = await apiClient.patch<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}`, {
        selected_concept_id: conceptId,
        quick_mode: false,
        expected_revision: session.revision,
      })
      const constraints = await apiClient.post<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}/stages/constraints/confirm`, {
        data: selection.data.data.draft?.form,
        confirm: true,
        source: 'author',
        expected_revision: selection.data.data.revision,
      })
      const confirmation = await apiClient.post<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}/stages/concepts/confirm`, {
        data: { options: concepts, selected_concept_id: conceptId },
        confirm: true,
        source: 'author',
        expected_revision: constraints.data.data.revision,
      })
      setSession(confirmation.data.data)
      message.success('当前创意方向已确认；不会自动生成其他数据')
    } catch (error) {
      message.error(errorText(error))
    } finally {
      setBusy(false)
    }
  }

  const confirmCurrentStage = async (continueToNext: boolean) => {
    if (!session || !currentStageState?.data) return
    setBusy(true)
    setStageActionError('')
    try {
      if (continueToNext) {
        const response = await apiClient.post<ApiResponse<{
          action_type: 'confirm_and_generate_recommended'
          session: CreationSession
          run: StageRun | null
          recommended_stage?: string | null
        }>>(`/novel-creation/sessions/${session.id}/stages/${currentStage}/confirm-and-generate-recommended`, {
          data: currentStageState.data,
          confirm: true,
          source: 'author',
          expected_revision: session.revision,
          model: selectedModel || null,
          use_model: true,
        }, { headers: { 'Idempotency-Key': `confirm-next:${session.id}:${currentStage}:${session.revision}` } })
        const refreshed = response.data.data.session
        setSession(refreshed)
        setBusy(false)
        const nextRun = response.data.data.run
        if (nextRun) {
          viewStage(nextRun.stage)
          setActiveRun(nextRun)
          watchRun(nextRun.id, nextRun.session_id || refreshed.id)
        } else {
          viewStage(currentStage, true)
          focusStageHeading()
        }
        return
      }
      const response = await apiClient.post<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}/stages/${currentStage}/confirm`, {
        data: currentStageState.data,
        confirm: true,
        source: 'author',
        expected_revision: session.revision,
      })
      const refreshed = response.data.data
      setSession(refreshed)
      setBusy(false)
      viewStage(currentStage, true)
      focusStageHeading()
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

  const openEditor = (stage = currentStage) => {
    const data = stage === 'concepts'
      ? (session?.draft?.stages?.concepts?.data || { options: concepts, selected_concept_id: selectedConceptId || null })
      : (session?.draft?.stages?.[stage]?.data || {})
    setEditorStage(stage)
    setEditorData(data)
    const serialized = JSON.stringify(data, null, 2)
    setEditorText(serialized)
    editorOriginalTextRef.current = serialized
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
      const response = await apiClient.patch<ApiResponse<CreationSession>>(`/novel-creation/sessions/${session.id}/stages/${editorStage}`, {
        data,
        source: 'author',
        expected_revision: session.revision,
      })
      setSession(response.data.data)
      if (CORE_STAGES.includes(editorStage)) viewStage(editorStage, true)
      setEditorOpen(false)
      message.success('修改已保存，下游阶段已按需标记为待重新生成')
    } catch (error) {
      message.error(error instanceof SyntaxError ? '结构内容不是有效 JSON，请检查括号和引号' : errorText(error))
    }
  }

  const closeEditor = () => {
    if (editorText === editorOriginalTextRef.current) {
      setEditorOpen(false)
      return
    }
    Modal.confirm({
      title: '放弃尚未保存的修改？',
      content: '关闭后，本次在完整编辑器中的修改不会写入立项数据。',
      okText: '放弃修改',
      okButtonProps: { danger: true },
      cancelText: '继续编辑',
      onOk: () => setEditorOpen(false),
    })
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

  const openRefine = (stage: string) => {
    setRefineTarget(stage)
    setRefineInstruction('')
    setRefineOpen(true)
  }

  const submitRefinement = async () => {
    const instruction = refineInstruction.trim()
    if (!instruction) {
      message.warning('请先写明希望 AI 如何调整')
      return
    }
    if (instruction.length > 2000) {
      message.warning('调整要求不能超过 2000 字')
      return
    }
    setRefineOpen(false)
    if (refineTarget === 'concepts') await generateConcepts('refine', instruction)
    else await startStageRun(refineTarget, false, session, 'refine', instruction)
  }

  const resetWorkspace = () => {
    invalidateSessionLoads()
    clearRunState()
    setSession(null)
    setCreationPath(null)
    setSearchParams({}, { replace: true })
    loadedSessionIdRef.current = null
    formDirtyRef.current = false
    editedDuringRunRef.current = false
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
  const authorLed = (session?.draft?.creation_mode || creationPath) === 'author_led'
  const showPathChooser = !session && !creationPath

  return (
    <div className="creation-page">
      <div className="creation-page-inner">
        <SystemNav current="creation" />
        <header className="creation-header">
          <div>
            <Title level={2}><BookOutlined /> 新书立项工作台</Title>
            <Paragraph>{authorLed ? '从你的原始设定出发，只补全空白，再逐步确认世界、角色与全书结构。' : '先比较创意，再逐步确认世界、角色与全书结构。'}正式作品只在最终确认时创建。</Paragraph>
          </div>
          <Space wrap>
            {session && <Button icon={<RobotOutlined />} onClick={() => navigate(assistantReturnUrl)}>返回原对话</Button>}
            {session && <Tag color="processing">草稿修订 {session.revision}</Tag>}
            {saveNotice && <Tag color={saveNotice.includes('失败') ? 'warning' : 'default'}>{saveNotice}</Tag>}
            {!inWorkbench && hasModels && modelOptions.length > 1 && <Select aria-label="选择本阶段模型" loading={modelsLoading} value={selectedModel} onChange={setSelectedModel} options={modelOptions} placeholder="切换可用模型" style={{ minWidth: 260 }} />}
            {!inWorkbench && hasModels && modelOptions.length === 1 && <Tag color="success">AI 已准备好</Tag>}
            <Button icon={<SettingOutlined />} onClick={() => navigate('/settings')}>配置模型</Button>
            {!session && creationPath && <Button onClick={() => setCreationPath(null)}>重新选择起点</Button>}
            {session && <Button onClick={resetWorkspace} disabled={busy}>新建立项</Button>}
          </Space>
        </header>

        {!hasModels && (
          <Alert className="creation-model-alert" type="warning" showIcon message="当前没有可用模型" description="你仍可填写并保存立项草稿。也可以先免费体验，不需要命令行或 API Key。" action={<Button type="primary" onClick={() => navigate('/getting-started')}>免费开始</Button>} />
        )}

        {showPathChooser ? (
          <main className="creation-path-shell" aria-labelledby="creation-path-title">
            <div className="creation-path-heading">
              <Text className="creation-path-kicker">选择起点</Text>
              <Title level={2} id="creation-path-title">你已经想到哪一步了？</Title>
              <Paragraph>司命会根据你的准备程度进入对应流程。已有设定不会被随机方案覆盖。</Paragraph>
            </div>
            <div className="creation-path-grid">
              <button type="button" className="creation-path-card is-primary" onClick={() => setCreationPath('author_led')}>
                <span className="creation-path-icon"><FileTextOutlined /></span>
                <span className="creation-path-badge">推荐给已有想法的作者</span>
                <strong>按我的设定立项</strong>
                <span>粘贴已有故事方案与大纲，锁定不可改动内容；AI 只负责整理和补全。</span>
                <em>开始整理我的方案</em>
              </button>
              <button type="button" className="creation-path-card" onClick={() => setCreationPath('explore')}>
                <span className="creation-path-icon"><CompassOutlined /></span>
                <strong>帮我探索创意</strong>
                <span>从一个画面或念头出发，生成一套故事方向；之后可随时通过对话局部调整。</span>
                <em>生成一个方向</em>
              </button>
              <button type="button" className="creation-path-card" onClick={() => navigate('/dashboard?create=import')}>
                <span className="creation-path-icon"><UploadOutlined /></span>
                <strong>导入已有小说</strong>
                <span>导入 TXT 或 DOCX，自动拆章建档，再进入写作工作台续写或二创。</span>
                <em>选择小说文件</em>
              </button>
            </div>
            {otherSessions.length > 0 && (
              <section className="creation-resume-band creation-path-resume">
                <Title level={4}>或继续未完成立项</Title>
                <div className="creation-resume-list">
                  {otherSessions.slice(0, 4).map((item) => (
                    <Card size="small" key={item.id} onClick={() => void loadSession(item.id)} hoverable title={item.draft?.form?.brief?.slice(0, 30) || '未命名立项'}>
                      <Text type="secondary">{item.draft?.form?.genre || '自由创作'} · {item.updated_at ? new Date(item.updated_at).toLocaleString('zh-CN') : '刚刚保存'}</Text>
                    </Card>
                  ))}
                </div>
              </section>
            )}
          </main>
        ) : !session || concepts.length === 0 ? (
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
                <div className="creation-form-heading"><div><Title level={3}>{authorLed ? '把你已经想好的内容交给司命' : '把故事的边界说清楚'}</Title><Text type="secondary">{authorLed ? '原文会作为作者事实持续保留，AI 不会把它替换成随机故事。' : '题材画像会自动填入可编辑约束，任何字段都不是强制答案。'}</Text></div>{activePreset && <Tooltip title="恢复该题材的原始预设"><Button icon={<ReloadOutlined />} onClick={() => applyPreset(activePreset)}>恢复预设</Button></Tooltip>}</div>
                {authorLed && <Alert className="creation-author-promise" type="success" showIcon icon={<LockOutlined />} message="作者原始设定优先" description="专名、核心因果、结局方向和下方锁定项会随每个阶段一同发送给 AI；它只能补全没有写明的部分。" />}
                <Form.Item name="brief" label={authorLed ? '已有故事方案' : '故事梗概或最想写的画面'} rules={[{ required: true, message: '至少写一句你想创作的故事' }]}>
                  <TextArea rows={6} maxLength={5000} showCount placeholder="例如：一个能看见病毒记忆的女孩，在被感染的城市里寻找失踪的母亲。她每救一个人，自己就会忘掉一段过去。" />
                </Form.Item>
                {authorLed && (
                  <div className="creation-author-fields">
                    <Form.Item name="author_outline" label="已有大纲" extra="可粘贴分卷、章节节点或结局安排，最多 20000 字。">
                      <TextArea rows={7} maxLength={20000} showCount placeholder={'第一卷：……\n关键转折：……\n结局：……'} />
                    </Form.Item>
                    <Form.Item
                      name="locked_requirements"
                      label="不可改动的设定"
                      getValueFromEvent={(event) => splitLines(event.target.value)}
                      getValueProps={(value) => ({ value: Array.isArray(value) ? value.join('\n') : '' })}
                      extra="每行一条，例如角色姓名、世界规则、CP、卷数或结局。"
                    >
                      <TextArea aria-label="不可改动的设定" rows={4} maxLength={5000} placeholder={'周遥必须是植物学实习生\n花展在七天后举行\n全书规划为六卷'} />
                    </Form.Item>
                  </div>
                )}
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
                  <Button size="large" type="primary" icon={<RocketOutlined />} loading={busy} disabled={!hasModels || !selectedModel} onClick={() => void generateConcepts('generate')}>{authorLed ? '整理为作者方案' : '生成创意方向'}</Button>
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
            {authorLed && session?.draft && (
              <section className="creation-author-source" aria-label="作者原始设定">
                <div><LockOutlined /><Text strong>作者原始设定</Text><Tag color="success">持续锁定</Tag></div>
                <Paragraph>{session.draft.author_brief || session.draft.form.brief}</Paragraph>
                {session.draft.author_outline && <Collapse ghost items={[{ key: 'outline', label: '查看已有大纲原文', children: <pre>{session.draft.author_outline}</pre> }]} />}
                <Space wrap>{session.draft.locked_requirements?.map((item) => <Tag key={item}>{item}</Tag>)}</Space>
              </section>
            )}
            <div className="creation-section-heading"><div><Title level={3}>{authorLed ? '检查作者方案' : '完善故事发动机'}</Title><Paragraph>{authorLed ? 'AI 只整理和补全了你的方案。继续前可手动编辑，或写明要求让 AI 定向调整。' : '这里先形成一套清晰方向。你可以继续对话调整，不需要在多套方案之间抽选。'}</Paragraph></div><Space wrap><Button onClick={() => openEditor('concepts')} disabled={busy}>编辑方案内容</Button><Button icon={<EditOutlined />} onClick={() => openRefine('concepts')} disabled={busy}>让 AI 按要求调整</Button><Button icon={<ReloadOutlined />} onClick={() => void generateConcepts('regenerate')} loading={busy}>{authorLed ? '重新整理方案' : '重新生成方向'}</Button></Space></div>
            <div className={`creation-concept-grid is-single ${authorLed ? 'is-author-led' : ''}`}>
              {concepts.map((concept) => (
                <Card key={concept.id} className="creation-concept-card" title={<span>{concept.title}</span>} extra={<Tag>{concept.coverage?.score || 0}% 覆盖</Tag>}>
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
                    <Button icon={<RobotOutlined />} onClick={() => navigate(assistantReturnUrl)} disabled={busy}>返回聊天继续调整</Button>
                    <Button type="primary" onClick={() => void confirmConceptOnly(concept.id)} disabled={busy}>确认当前方向</Button>
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
                  const canView = (
                    session?.stage_flow?.items?.[stage]?.can_view
                    || session?.stage_flow?.items?.[stage]?.can_generate
                  ) ?? Boolean(session?.draft?.stages?.[stage]?.data || stage === currentStage)
                  if (canView) viewStage(stage)
                }}
                items={CORE_STAGES.map((stage) => {
                  const state = session?.draft?.stages[stage]
                  const flow = session?.stage_flow?.items?.[stage]
                  const canView = (flow?.can_view || flow?.can_generate)
                    ?? Boolean(state?.data || stage === currentStage)
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
                        {!canView && flow?.blocked_by?.[0] && <Text type="secondary">需先处理{flow.blocked_by[0].label}</Text>}
                      </Space>
                    ),
                  }
                })}
              />
              <Alert type="info" showIcon message={session?.draft?.quick_mode ? '快速模式' : '自由规划'} description="各项内容可以按任意顺序生成；缺少关联资料时只会影响生成质量。最终确认前不会创建正式作品。" />
            </aside>
            <section className="creation-stage-main">
              {authorLed && session.draft && (
                <section className="creation-author-source is-compact" aria-label="作者原始设定">
                  <div><LockOutlined /><Text strong>作者原始设定持续生效</Text><Tag color="success">不可静默改写</Tag></div>
                  <Paragraph ellipsis={{ rows: 2, expandable: true, symbol: '展开原文' }}>{session.draft.author_brief || session.draft.form.brief}</Paragraph>
                  {session.draft.author_outline && (
                    <Collapse
                      className="creation-author-outline"
                      ghost
                      size="small"
                      items={[{
                        key: 'author-outline',
                        label: '查看已有大纲原文',
                        children: <pre>{session.draft.author_outline}</pre>,
                      }]}
                    />
                  )}
                  <Space wrap>{session.draft.locked_requirements?.map((item) => <Tag key={item}>{item}</Tag>)}</Space>
                </section>
              )}
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
                  <Button icon={<ReloadOutlined />} onClick={() => void startStageRun(currentStage, false, session, 'regenerate')} disabled={busy || currentStageFlow?.can_generate === false}>重新生成</Button>
                  <Button onClick={() => openRefine(currentStage)} disabled={!currentStageState?.data || busy}>让 AI 按要求调整</Button>
                  <Button icon={<EditOutlined />} onClick={() => openEditor()} disabled={!currentStageState?.data || busy}>编辑阶段内容</Button>
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

        <RunStatusPanels
          busy={busy}
          activeRun={activeRun}
          stageLabels={stageLabels}
          runMessage={runMessage}
          runConnection={runConnection}
          runProgress={runProgress}
          editedDuringRun={editedDuringRunRef.current}
          cancellingRun={cancellingRun}
          pausingRun={pausingRun}
          resultRevisionNotice={resultRevisionNotice}
          onCancel={() => void cancelActiveRun()}
          onPause={() => void pauseActiveRun()}
          onResume={() => void resumeActiveRun()}
          onAcceptResult={() => { editedDuringRunRef.current = false; setResultRevisionNotice('') }}
          onRegenerateLatest={() => activeRun?.stage === 'concepts'
            ? void generateConcepts()
            : void startStageRun(activeRun?.stage || currentStage)}
        />

        {session?.last_error && !busy && (() => {
          const failedStage = session.last_error.failed_stage
            || session.runs?.find((run) => run.id === session.last_error?.run_id)?.stage
          const retryStage = failedStage && [...CORE_STAGES, 'concepts'].includes(failedStage) ? failedStage : currentStage
          const retryLabel = session.last_error.failed_stage_label || stageLabels[retryStage] || retryStage
          const retryBlocker = session.stage_flow?.items?.[retryStage]?.blocked_by?.[0]
          return (
            <Alert
              className="creation-error-band"
              type="error"
              showIcon
              message={session.last_error.message || '阶段运行失败'}
              description={session.last_error.next_action}
              action={retryBlocker
                ? <Button onClick={() => viewStage(retryBlocker.stage)}>先修复“{retryBlocker.label}”</Button>
                : <Button onClick={() => retryStage === 'concepts' ? void generateConcepts() : void startStageRun(retryStage)}>重试“{retryLabel}”</Button>}
            />
          )
        })()}
      </div>

      <Modal
        className="creation-editor-modal"
        title={`完整编辑器 · ${stageLabels[editorStage] || (editorStage === 'concepts' ? '创意方向' : editorStage)}`}
        open={editorOpen}
        onCancel={closeEditor}
        onOk={saveEditor}
        okText="保存并同步"
        cancelText="取消"
        width={1120}
        maskClosable={false}
        keyboard={false}
        destroyOnHidden
      >
        <Alert type="info" showIcon message="只展开需要修改的部分" description="列表默认收起，先定位再编辑可以减少误改。保存后，受影响的下游内容会标记为需要重新校验；内部关联标识由司命维护。" />
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

      <Modal
        title={`让 AI 调整：${stageLabels[refineTarget] || (refineTarget === 'concepts' ? '创意方向' : refineTarget)}`}
        open={refineOpen}
        onCancel={() => setRefineOpen(false)}
        onOk={() => void submitRefinement()}
        okText="按要求调整"
        confirmLoading={busy}
        destroyOnHidden
      >
        <Alert type="info" showIcon message="只修改当前阶段" description="司命会同时带上当前内容、已确认的上游阶段和作者锁定要求。生成或校验失败时，当前草稿不会被覆盖。" />
        <Form.Item label="调整要求" required style={{ marginTop: 20 }}>
          <TextArea
            autoFocus
            rows={6}
            maxLength={2000}
            showCount
            value={refineInstruction}
            onChange={(event) => setRefineInstruction(event.target.value)}
            placeholder="例如：改成六卷结构；保留周遥的职业和花展结局；加强第二卷取证压力，但不要改变结局。"
          />
        </Form.Item>
      </Modal>

    </div>
  )
}

export default NovelCreationWizardPage
