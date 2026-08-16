/**
 * Desktop control-panel assistant.
 *
 * It runs outside the project workspace page. When a project is selected it
 * uses the normal workspace assistant; without a project it behaves as a
 * system-level assistant that can help users create the first novel project.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Empty,
  Modal,
  Input,
  Popover,
  Progress,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd'
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  DatabaseOutlined,
  FileAddOutlined,
  FolderOpenOutlined,
  HistoryOutlined,
  InfoCircleOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PlusOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RocketOutlined,
  RobotOutlined,
  SendOutlined,
  StopOutlined,
  UndoOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { StructuredStageEditor } from './novel-creation/StructuredStageEditor'
import { useModelOptions } from '../hooks/useModelOptions'
import { motionAwareScrollBehavior } from '../utils/motion'
import { extractExplicitLocalPaths } from '../utils/localCliPathGrant'
import { apiDateTimeMs } from '../utils/dateTime'
import { AssistantMessageTime } from './assistant/MessageTime'
import { startNovelCreationSession } from '../services/novelCreationAgent'
import {
  defaultCreationAgentRuntime,
  extractCreationAgentErrorDetail,
  formatSystemAssistantError,
  type CreationAgentRuntime,
} from '../utils/creationAgent'
import './GuiAssistantChat.css'
import './assistant/MessageTime.css'

const { Title, Paragraph, Text } = Typography
const EMPTY_ASSISTANT_REPLY = '没有收到模型的文字回复。请重试一次，或在系统设置里测试当前模型/CLI 是否支持项目助手的流式输出和工具调用。'
const CHAT_MESSAGE_CHAR_LIMIT = 1_000_000
const LONG_CREATION_TEXT_THRESHOLD = 20_000
const LOCAL_CLI_WRITE_INTENT = /(?:修改|改成|改为|调整|更新|写入|保存|补充|新增|生成|删除|移除|替换|确认|锁定|解锁|撤销|恢复|导入|创建|建项|立项|回改|设置)/

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface Project {
  id: string
  title: string
  updated_at?: string
}

interface CreationSessionContext {
  id: string
  user_brief?: string
  display_title?: string
  status: string
  revision: number
  created_project_id?: string | null
  source_project_id?: string | null
  updated_at?: string | null
  created_at?: string | null
}

interface Conversation {
  id: string
  project_id?: string
  title: string
  scope?: string
  scope_type?: 'system' | 'creation' | 'project'
  scope_id?: string
  creation_session_id?: string
  user_brief?: string
  blueprints?: NovelBlueprint[]
  message_count?: number
  created_at?: string
  updated_at?: string
}

interface PersistedMessage {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string
  status?: string
  run_id?: string | null
  operation_id?: string | null
  message_type?: string
  payload?: { reply?: string; run?: NovelCreationRunSummary; question?: ChatQuestion } | null
  created_at?: string
}

interface ChatQuestion { question: string; purpose?: string; options?: string[]; type?: 'single_select' | 'multi_select' | 'text' }

interface ChatMessage {
  id?: string
  role: 'user' | 'assistant'
  content: string
  status?: string
  created_at?: string
  questions?: ChatQuestion[]
  messageType?: string
  run?: NovelCreationRunSummary
}

interface NovelCreationRunSummary {
  id: string
  run_id?: string
  session_id?: string
  stage: string
  status: string
  current_message?: string
  next_action?: string
  failure_class?: string
  operation_id?: string
  model_source?: string
  attempt?: number
  result_mode?: string
  card_presentation?: {
    status: 'queued' | 'running' | 'waiting_user' | 'paused' | 'completed' | 'partial_success' | 'failed' | 'cancelled' | 'interrupted'
    label: string
    message: string
    show_retry: boolean
    judged_by: 'model' | 'fallback'
    reason?: string
    raw_status?: string
    model?: string
  }
}

interface CreationArtifactSummary {
  artifact: string
  label: string
  status: 'pending' | 'generated' | 'confirmed' | 'stale' | 'conflict'
  stored_status?: 'pending' | 'generated' | 'confirmed' | 'stale'
  conflict?: { run_id: string; message?: string; candidate_available?: boolean; input_revision?: number; current_revision?: number } | null
  source?: string
  updated_at?: string
  stale_reason?: string
  locked_paths?: string[]
  checkpoint_count?: number
  can_undo?: boolean
  latest_checkpoint_at?: string
  version_count?: number
  latest_version_id?: string | null
  revision: number
  flow?: {
    can_view?: boolean
    can_generate?: boolean
    can_confirm?: boolean
    blocked_by?: Array<{ stage: string; label: string; reason: string }>
    soft_dependencies?: Array<{ stage: string; label: string; reason: string; message: string }>
  }
  running_operation?: NovelCreationRunSummary | null
}

interface CreationArtifactDetail extends CreationArtifactSummary {
  data?: Record<string, unknown> | null
}

function emptyArtifactData(artifact: string): Record<string, unknown> {
  const templates: Record<string, Record<string, unknown>> = {
    concepts: { options: [], selected_concept_id: null },
    constraints: { special_requirements: [], avoid: [] },
    world_style: { world_tone: '', story_structure: '', pacing: '', writing_style: '', worldbuilding: [] },
    characters: { characters: [], relationships: [] },
    locations: { entries: [], relations: [] },
    macro_outline: { story_overview: '', core_conflict: '', volumes: [] },
    opening_outline: { chapters: [], sections: [] },
    final_review: { ready: false, blocking: [], warnings: [], counts: {} },
  }
  return templates[artifact] || {}
}

interface CreationConsistencyReport {
  valid: boolean
  revision: number
  summary: { blocking: number; warnings: number; total: number }
  issues: Array<{ code: string; severity: 'error' | 'warning'; message: string; artifact?: string; entity_id?: string }>
}

interface CreationArtifactVersionSummary {
  id: string
  artifact: string
  revision: number
  status: string
  source: string
  change_type: string
  change_summary?: Array<Record<string, unknown>>
  parent_version_id?: string | null
  restored_from_version_id?: string | null
  created_at?: string
}

interface CreationArtifactVersionDetail {
  version: CreationArtifactVersionSummary
  against?: CreationArtifactVersionSummary | null
  changes: Array<{ path: string; action: string; before?: unknown; after?: unknown }>
  change_count: number
  truncated: boolean
  snapshot?: Record<string, unknown>
}

interface MaterialImportSummary {
  id: string
  source_file_id: string
  session_id: string
  operation_id?: string
  filename: string
  status: 'queued' | 'running' | 'waiting_user' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
  text_length: number
  chunk_count: number
  processed_chunks: number
  input_revision: number
  error?: string
  preview?: {
    detected?: { characters?: number; factions?: number; locations?: number; volumes?: number; chapter_summaries?: number }
    artifact_counts?: Record<string, number>
    available_artifacts?: string[]
    conflicts?: Array<{ kind?: string; artifact?: string; status?: string }>
  }
  result?: { applied?: Array<{ artifact: string; count: number }>; skipped?: Array<{ artifact: string; reason: string }>; revision?: number }
}

interface PendingMaterialFile {
  name: string
  size: number
  file: File
  content: string
}

type AssistantInputRoute = 'creation_material' | 'new_project_import' | 'reference' | 'chat_only' | 'clarify'

interface AssistantInputRouteDecision {
  route: AssistantInputRoute
  resolved_instruction: string
  clarification_question: string
  reason?: string
  confidence?: number
  classification_status?: 'model' | 'safe_fallback'
  source_context?: string
  source_coverage?: {
    coverage?: 'full' | 'distributed'
    source_chars?: number
    included_chars?: number
    omitted_chars?: number
  }
}

interface PendingInputClarification {
  source: PendingMaterialFile
  sourceKind: 'long_text' | 'attachment'
  originalInstruction: string
  exchanges: Array<{ question: string; answer: string }>
  currentQuestion: string
}

interface NovelBlueprint {
  title: string
  subtitle?: string
  logline?: string
  premise?: string
  genre?: string
  creation_engine?: string
  estimated_chapters?: number
  protagonist?: {
    name?: string
    goal?: string
    conflict?: string
    personality?: string
    background?: string
    weakness?: string
    opening_pressure?: string
  }
  characters?: unknown[]
  relationships?: unknown[]
  worldbuilding?: unknown[]
  volume_outline?: Array<{ title?: string; summary?: string; start_chapter?: number; end_chapter?: number }>
  outline?: unknown[]
  selling_points?: string[]
  world_hook?: string
  core_conflict?: string
  golden_three?: {
    opening_scene?: string
    chapter_1?: string
    chapter_2?: string
    chapter_3?: string
    promise?: string
  }
  creative_slots?: Record<string, unknown>
  requirement_coverage?: { score?: number; covered?: string[]; missing?: string[]; warnings?: string[] }
  quality_self_check?: { score?: number; pass?: boolean; issues?: string[]; suggestions?: string[] }
}

const PROJECT_STORAGE_KEY = 'siming.gui.assistant.projectId'
const LEGACY_PROJECT_STORAGE_KEY = 'moshu.gui.assistant.projectId'
const SIDEBAR_STORAGE_KEY = 'siming.gui.assistant.sidebarCollapsed'
const LEGACY_SIDEBAR_STORAGE_KEY = 'moshu.gui.assistant.sidebarCollapsed'
const CREATION_TEMPLATE_KEY = 'siming:novelCreationTemplates'
const LEGACY_CREATION_TEMPLATE_KEY = 'moshu:novelCreationTemplates'

interface CreationTemplate {
  id: string
  name: string
  brief: string
  creative_slots?: Record<string, string | string[]>
}

function slotValueToText(value: string | string[] | undefined): string {
  if (Array.isArray(value)) return value.join('；')
  return value || ''
}

function slotDraftToFeedback(slots: Record<string, string | string[]>): string {
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
    const raw = localStorage.getItem(CREATION_TEMPLATE_KEY) || localStorage.getItem(LEGACY_CREATION_TEMPLATE_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function shouldUseNovelCreation(text: string, hasActiveProject: boolean) {
  const normalized = text.trim()
  if (!normalized) return false
  if (hasActiveProject && /(写|续写|重写|生成).{0,8}第?\d+\s*章/.test(normalized)) {
    return false
  }
  if (/新书|新小说|新作品|帮我创建|创建.*(小说|作品|项目)|新建.*(小说|作品|项目)|设计.*(小说|新书)|开书|立项|我想写|我要写|帮我写.*小说|\d{3,5}\s*章.*创意/.test(normalized)) {
    return true
  }
  if (!hasActiveProject && /(克苏鲁|规则怪谈|修仙|仙侠|玄幻|都市|科幻|悬疑|言情|历史|末日|赛博|无限流)/.test(normalized)) {
    return true
  }
  return false
}

function requestsFreshNovelContext(text: string) {
  return /(一本新的|新的一本|另(?:开|建|写)一本|再(?:创建|开|写)一本|重新(?:创建|立项)|创建.*新(?:小说|作品)|新建.*(?:小说|作品))/.test(text)
}

function GuiAssistantChat() {
  const navigate = useNavigate()
  const location = useLocation()
  const requestedCreationSession = new URLSearchParams(location.search).get('creationSession') || ''
  const requestedConversationId = new URLSearchParams(location.search).get('conversation') || ''
  const requestedArtifact = new URLSearchParams(location.search).get('artifact') || ''
  const openedRequestedArtifactRef = useRef<string>('')
  const [projects, setProjects] = useState<Project[]>([])
  const [creationSessions, setCreationSessions] = useState<CreationSessionContext[]>([])
  const [activeProjectId, setActiveProjectId] = useState<string>()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConvId, setActiveConvId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [inputValue, setInputValue] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [loading, setLoading] = useState(false)
  const [projectsLoading, setProjectsLoading] = useState(true)
  const [conversationsLoading, setConversationsLoading] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => {
      const stored = localStorage.getItem(SIDEBAR_STORAGE_KEY) || localStorage.getItem(LEGACY_SIDEBAR_STORAGE_KEY)
      return stored === null ? true : stored === '1'
    },
  )
  const [systemConversationId, setSystemConversationId] = useState<string>()
  const systemConversationIdRef = useRef<string>()
  const creationArtifactRequestRef = useRef(0)
  const creationRunPresentationRequestRef = useRef(0)
  const creationRunPresentationAttemptRef = useRef(new Set<string>())
  const pendingCreationContextRef = useRef<string>()
  const [systemBlueprints, setSystemBlueprints] = useState<NovelBlueprint[]>([])
  const [runningStartTime, setRunningStartTime] = useState<number | null>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [pendingFiles, setPendingFiles] = useState<PendingMaterialFile[]>([])
  const [pendingInputClarification, setPendingInputClarification] = useState<PendingInputClarification | null>(null)
  const [activeMaterialImport, setActiveMaterialImport] = useState<MaterialImportSummary | null>(null)
  const [importPreviewOpen, setImportPreviewOpen] = useState(false)
  const [selectedImportArtifacts, setSelectedImportArtifacts] = useState<string[]>([])
  const [importStrategy, setImportStrategy] = useState<'merge' | 'overwrite_unconfirmed' | 'skip_conflicts'>('merge')
  const [importActionLoading, setImportActionLoading] = useState(false)
  const [activeCreationRun, setActiveCreationRun] = useState<NovelCreationRunSummary | null>(null)
  const [creationRunAction, setCreationRunAction] = useState<'cancel' | 'pause' | 'resume' | 'retry-original' | 'retry-latest' | null>(null)
  const [creationArtifacts, setCreationArtifacts] = useState<CreationArtifactSummary[]>([])
  const [creationArtifactsLoading, setCreationArtifactsLoading] = useState(false)
  const [creationConsistency, setCreationConsistency] = useState<CreationConsistencyReport | null>(null)
  const [creationConsistencyLoading, setCreationConsistencyLoading] = useState(false)
  const [creationPanelOpen, setCreationPanelOpen] = useState(() => (
    typeof window === 'undefined' || typeof window.matchMedia !== 'function'
      ? true
      : !window.matchMedia('(max-width: 1180px)').matches
  ))
  const [artifactAction, setArtifactAction] = useState<string | null>(null)
  const [versionHistoryArtifact, setVersionHistoryArtifact] = useState<CreationArtifactSummary | null>(null)
  const [artifactVersions, setArtifactVersions] = useState<CreationArtifactVersionSummary[]>([])
  const [selectedArtifactVersion, setSelectedArtifactVersion] = useState<CreationArtifactVersionDetail | null>(null)
  const [versionHistoryLoading, setVersionHistoryLoading] = useState(false)
  const [artifactDetailLoading, setArtifactDetailLoading] = useState(false)
  const [expandedArtifact, setExpandedArtifact] = useState<CreationArtifactDetail | null>(null)
  const [expandedArtifactSessionId, setExpandedArtifactSessionId] = useState<string | null>(null)
  const [artifactEditorData, setArtifactEditorData] = useState<Record<string, unknown>>({})
  const [artifactEditorRevision, setArtifactEditorRevision] = useState<number | null>(null)
  const [artifactEditorDirty, setArtifactEditorDirty] = useState(false)
  const [artifactEditorSaving, setArtifactEditorSaving] = useState(false)
  const [artifactEditorError, setArtifactEditorError] = useState<string | null>(null)
  const [artifactEditorSavedAt, setArtifactEditorSavedAt] = useState<string | null>(null)
  const [agentRuntimeOverride, setAgentRuntimeOverride] = useState<Partial<CreationAgentRuntime>>({})

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const compactViewport = window.matchMedia('(max-width: 1180px)')
    const closePanelWhenCompact = (event: MediaQueryListEvent) => {
      // A compact rail may close to protect chat space, but a focused editor
      // is an active task. Keep it open across window resizing so the user's
      // form and unsaved working memory never appear to vanish.
      if (event.matches && !expandedArtifact) setCreationPanelOpen(false)
    }
    compactViewport.addEventListener('change', closePanelWhenCompact)
    return () => compactViewport.removeEventListener('change', closePanelWhenCompact)
  }, [expandedArtifact])

  const {
    defaultModel,
    modelOptions = [],
    loading: modelsLoading = false,
  } = useModelOptions()
  const [selectedModelOverride, setSelectedModelOverride] = useState<string>()
  const selectedModel = selectedModelOverride || defaultModel || undefined
  const selectedProvider = String(selectedModel || '').split(':', 1)[0]
  const isLocalCliModel = selectedProvider.endsWith('_cli')
  const supportsTransientCreationMcp = selectedProvider === 'opencode_cli'
  const [nextLocalCliGrant, setNextLocalCliGrant] = useState(false)
  const creationAgentModelSource = selectedModelOverride
    ? 'conversation_override'
    : selectedModel ? 'global_default' : 'unconfigured'

  useEffect(() => {
    setNextLocalCliGrant(false)
  }, [selectedModel])
  const [systemSessionId, setSystemSessionId] = useState<string>()
  const [systemBrief, setSystemBrief] = useState('')
  const adoptCreationSession = useCallback((sessionId: string, brief = '') => {
    setSystemSessionId(sessionId)
    if (brief) setSystemBrief(brief)
  }, [])
  const resetCreationSession = useCallback(() => {
    setSystemSessionId(undefined)
    setSystemBrief('')
  }, [])

  useEffect(() => {
    systemConversationIdRef.current = systemConversationId
  }, [systemConversationId])

  useEffect(() => {
    creationArtifactRequestRef.current += 1
    setCreationArtifacts([])
    setCreationConsistency(null)
    setActiveCreationRun(null)
    setActiveMaterialImport(null)
    setExpandedArtifact(null)
    setExpandedArtifactSessionId(null)
  }, [systemSessionId])

  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const resumedSessionId = params.get('creationSession') || ''
    const resumedImportId = params.get('import') || ''
    if (resumedSessionId && resumedSessionId !== systemSessionId) {
      adoptCreationSession(resumedSessionId)
    }
    if (resumedImportId) {
      void apiClient.get<ApiResponse<MaterialImportSummary>>(`/novel-creation/imports/${resumedImportId}`)
        .then((response) => {
          const importRun = response.data.data
          setActiveMaterialImport(importRun)
          if (importRun.status === 'waiting_user') {
            setSelectedImportArtifacts(importRun.preview?.available_artifacts || [])
          }
        })
        .catch(() => message.error('无法恢复资料导入状态，请从任务中心重试'))
    }
  }, [adoptCreationSession, location.search, systemSessionId])

  const openArtifactEditor = useCallback(async (artifact: CreationArtifactSummary, sessionIdOverride?: string) => {
    const targetSessionId = sessionIdOverride || systemSessionId || activeCreationRun?.session_id
    if (!targetSessionId) {
      message.error('找不到这项立项数据所属的会话')
      return
    }
    setArtifactDetailLoading(true)
    setArtifactEditorError(null)
    setExpandedArtifact({ ...artifact, data: emptyArtifactData(artifact.artifact) })
    setExpandedArtifactSessionId(targetSessionId)
    setArtifactEditorData(emptyArtifactData(artifact.artifact))
    setArtifactEditorRevision(artifact.revision)
    setArtifactEditorDirty(false)
    setArtifactEditorSavedAt(null)
    setCreationPanelOpen(true)
    if (targetSessionId !== systemSessionId) adoptCreationSession(targetSessionId)
    try {
      const response = await apiClient.get<ApiResponse<CreationArtifactDetail>>(
        `/novel-creation/sessions/${targetSessionId}/artifacts/${artifact.artifact}`,
      )
      const detail = response.data.data
      setExpandedArtifact(detail)
      setArtifactEditorData(detail.data || emptyArtifactData(detail.artifact))
      setArtifactEditorRevision(detail.revision)
      setArtifactEditorDirty(false)
    } catch (error: unknown) {
      setArtifactEditorError(error instanceof Error ? error.message : '未能读取已有数据；你仍可直接补充并自动保存')
    } finally {
      setArtifactDetailLoading(false)
    }
  }, [activeCreationRun?.session_id, adoptCreationSession, systemSessionId])

  const saveExpandedArtifact = useCallback(async () => {
    const targetSessionId = expandedArtifactSessionId || systemSessionId
    if (!targetSessionId || !expandedArtifact || artifactEditorRevision == null || !artifactEditorDirty || artifactEditorSaving) return
    setArtifactEditorSaving(true)
    setArtifactEditorError(null)
    try {
      const response = await apiClient.patch<ApiResponse<{ artifact: CreationArtifactDetail }>>(
        `/novel-creation/sessions/${targetSessionId}/artifacts/${expandedArtifact.artifact}`,
        {
          changes: [{ path: '/', action: 'replace', value: artifactEditorData }],
          source: 'author',
          expected_revision: artifactEditorRevision,
          allow_incomplete: true,
        },
      )
      const saved = response.data.data.artifact
      setExpandedArtifact((current) => current ? saved : null)
      setArtifactEditorRevision(saved.revision)
      setArtifactEditorDirty(false)
      setArtifactEditorSavedAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
      setCreationArtifacts((items) => items.map((item) => item.artifact === saved.artifact ? saved : item))
    } catch (error: unknown) {
      setArtifactEditorError(error instanceof Error ? error.message : '自动保存失败；内容仍保留在编辑器中')
    } finally {
      setArtifactEditorSaving(false)
    }
  }, [artifactEditorData, artifactEditorDirty, artifactEditorRevision, artifactEditorSaving, expandedArtifact, expandedArtifactSessionId, systemSessionId])

  useEffect(() => {
    if (!artifactEditorDirty) return
    const timer = window.setTimeout(() => void saveExpandedArtifact(), 5000)
    return () => window.clearTimeout(timer)
  }, [artifactEditorData, artifactEditorDirty, saveExpandedArtifact])

  const closeArtifactEditor = useCallback(async () => {
    if (artifactEditorDirty) await saveExpandedArtifact()
    setExpandedArtifact(null)
    setExpandedArtifactSessionId(null)
    setArtifactEditorError(null)
  }, [artifactEditorDirty, saveExpandedArtifact])

  const fetchCreationArtifacts = useCallback(async () => {
    if (!systemSessionId) {
      setCreationArtifacts([])
      return
    }
    const requestedSessionId = systemSessionId
    const requestId = ++creationArtifactRequestRef.current
    setCreationArtifactsLoading(true)
    try {
      const response = await apiClient.get<ApiResponse<{ artifacts: CreationArtifactSummary[] }>>(
        `/novel-creation/sessions/${requestedSessionId}/artifacts`,
      )
      if (creationArtifactRequestRef.current === requestId) {
        setCreationArtifacts(response.data.data.artifacts || [])
      }
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : '立项数据同步失败')
    } finally {
      if (creationArtifactRequestRef.current === requestId) setCreationArtifactsLoading(false)
    }
  }, [systemSessionId])

  const checkCreationConsistency = useCallback(async (showToast = false) => {
    if (!systemSessionId) return
    setCreationConsistencyLoading(true)
    try {
      const response = await apiClient.post<ApiResponse<CreationConsistencyReport>>(
        `/novel-creation/sessions/${systemSessionId}/validate-consistency`,
      )
      setCreationConsistency(response.data.data)
      if (showToast) {
        if (response.data.data.valid) message.success('立项数据一致性检查通过')
        else message.warning(`发现 ${response.data.data.summary.total} 项需要处理的内容`)
      }
    } catch (error: unknown) {
      if (showToast) message.error(error instanceof Error ? error.message : '一致性检查失败')
    } finally {
      setCreationConsistencyLoading(false)
    }
  }, [systemSessionId])

  useEffect(() => {
    void fetchCreationArtifacts()
    void checkCreationConsistency()
  }, [fetchCreationArtifacts, checkCreationConsistency, activeCreationRun?.status])

  useEffect(() => {
    if (!requestedArtifact || !systemSessionId || openedRequestedArtifactRef.current === `${systemSessionId}:${requestedArtifact}`) return
    const artifact = creationArtifacts.find((item) => item.artifact === requestedArtifact)
    if (!artifact) return
    openedRequestedArtifactRef.current = `${systemSessionId}:${requestedArtifact}`
    void openArtifactEditor(artifact)
  }, [creationArtifacts, openArtifactEditor, requestedArtifact, systemSessionId])

  useEffect(() => {
    if (!systemSessionId) {
      setActiveMaterialImport(null)
      return
    }
    let disposed = false
    const restoreLatestImport = async () => {
      try {
        const list = await apiClient.get<ApiResponse<{ imports: MaterialImportSummary[] }>>(
          `/novel-creation/sessions/${systemSessionId}/imports`,
        )
        const latest = list.data.data.imports?.[0]
        if (!latest || disposed) return
        const detail = await apiClient.get<ApiResponse<MaterialImportSummary>>(`/novel-creation/imports/${latest.id}`)
        if (!disposed) {
          const importRun = detail.data.data
          setActiveMaterialImport(importRun)
          if (importRun.status === 'waiting_user') {
            setSelectedImportArtifacts(importRun.preview?.available_artifacts || [])
          }
        }
      } catch {
        // A session created before durable imports legitimately has no imports.
      }
    }
    void restoreLatestImport()
    return () => { disposed = true }
  }, [systemSessionId])

  useEffect(() => {
    const importId = activeMaterialImport?.id
    if (!importId || !['queued', 'running'].includes(activeMaterialImport.status)) return
    let disposed = false
    const refresh = async () => {
      try {
        const response = await apiClient.get<ApiResponse<MaterialImportSummary>>(`/novel-creation/imports/${importId}`)
        if (disposed) return
        const next = response.data.data
        setActiveMaterialImport(next)
        if (next.status === 'waiting_user') {
          const available = next.preview?.available_artifacts || []
          setSelectedImportArtifacts(available)
          setImportPreviewOpen(true)
          message.success('资料已整理完成，请预览并选择要导入的数据')
        } else if (next.status === 'failed') {
          message.error(next.error || '资料导入失败，已完成的分块仍然保留')
        }
      } catch {
        // Operation and import state are durable; a later poll can recover.
      }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 1200)
    return () => {
      disposed = true
      window.clearInterval(timer)
    }
  }, [activeMaterialImport?.id, activeMaterialImport?.status])
  const creationAgentRuntime = {
    ...defaultCreationAgentRuntime(selectedModel, creationAgentModelSource),
    ...agentRuntimeOverride,
  }
  const recordAgentRuntimeError = (error: unknown) => {
    const detail = extractCreationAgentErrorDetail(error)
    const runtime = detail.runtime && typeof detail.runtime === 'object'
      ? detail.runtime as Partial<CreationAgentRuntime>
      : {}
    const failureClass = String(detail.failure_class || runtime.failure_class || '')
    setAgentRuntimeOverride({
      ...runtime,
      quota_status: failureClass === 'quota_or_rate_limit'
        ? 'exhausted_or_limited'
        : runtime.quota_status,
      failure_class: failureClass || undefined,
      next_action: String(detail.next_action || runtime.next_action || '') || undefined,
    })
  }
  const runtimeSourceLabel: Record<string, string> = {
    conversation_override: '本次对话覆盖',
    global_default: '全局默认',
    task_setting: '任务设置',
    task_setting_fallback: '任务设置回退',
    unconfigured: '未配置',
    unknown: '待确认',
  }
  const runtimeQuotaLabel = creationAgentRuntime.quota_status === 'exhausted_or_limited'
    ? '额度：已耗尽或限流'
    : '额度：未检测'
  const runtimeToolModeLabel = isLocalCliModel
    ? '工具模式：受控本机 Agent 工具桥'
    : '工具模式：Creation Agent 原生工具调用'
  // Creative slots editor state
  const [slotEditorOpen, setSlotEditorOpen] = useState(false)
  const [slotBlueprintIndex, setSlotBlueprintIndex] = useState<number | null>(null)
  const [slotDraft, setSlotDraft] = useState<Record<string, string | string[]>>({})
  const [creationTemplates, setCreationTemplates] = useState<CreationTemplate[]>([])

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const activeProjectOperationRef = useRef<string | null>(null)
  const projectStopRequestedRef = useRef(false)
  const creationRunMessageRef = useRef<{ conversationId: string; assistantMessageId: string } | null>(null)

  // Elapsed timer for "running" status messages
  useEffect(() => {
    if (!runningStartTime) {
      setElapsedSeconds(0)
      return
    }
    const interval = setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - runningStartTime) / 1000))
    }, 1000)
    return () => clearInterval(interval)
  }, [runningStartTime])

  useEffect(() => {
    const runId = activeCreationRun?.id || activeCreationRun?.run_id
    if (!runId || !['queued', 'running'].includes(activeCreationRun.status)) return
    let source: EventSource | null = null
    let pollTimer: ReturnType<typeof setInterval> | null = null
    const applyRun = (nextRun: NovelCreationRunSummary) => {
        setActiveCreationRun(nextRun)
        setMessages((previous) => previous.map((item) => (
          item.run && (item.run.id || item.run.run_id) === runId
            ? { ...item, run: nextRun, content: nextRun.current_message || item.content }
            : item
        )))
        if (['waiting_user', 'waiting_author', 'completed', 'failed', 'cancelled', 'interrupted'].includes(nextRun.status)) {
          const binding = creationRunMessageRef.current
          if (binding) {
            const presentedStatus = nextRun.card_presentation?.status
            const messageStatus = presentedStatus === 'failed' || (!presentedStatus && nextRun.status === 'failed')
              ? 'error'
              : presentedStatus === 'cancelled' || (!presentedStatus && nextRun.status === 'cancelled')
                ? 'cancelled'
                : presentedStatus === 'interrupted' || (!presentedStatus && nextRun.status === 'interrupted')
                  ? 'interrupted'
                  : 'completed'
            void finishSystemTurn(
              binding.conversationId,
              binding.assistantMessageId,
              nextRun.card_presentation?.message
                || nextRun.current_message
                || '立项任务状态已更新',
              messageStatus,
              {
                creationSessionId: nextRun.session_id || systemSessionId,
                userBrief: systemBrief,
                messageType: 'operation',
                run: nextRun,
              },
            )
          }
          source?.close()
          if (pollTimer) clearInterval(pollTimer)
        }
    }
    const updateRun = (event: MessageEvent) => {
      try {
        applyRun(JSON.parse(event.data) as NovelCreationRunSummary)
      } catch {
        // A reconnect will deliver a fresh snapshot; malformed events are ignored.
      }
    }
    if (typeof EventSource !== 'undefined') {
      source = new EventSource(`/api/v1/novel-creation/runs/${runId}/stream`)
      source.addEventListener('snapshot', updateRun as EventListener)
      source.addEventListener('done', updateRun as EventListener)
    } else {
      const poll = async () => {
        try {
          const response = await apiClient.get<ApiResponse<NovelCreationRunSummary>>(
            `/novel-creation/runs/${runId}`,
          )
          applyRun(response.data.data)
        } catch {
          // Keep the last durable state visible while the connection recovers.
        }
      }
      void poll()
      pollTimer = setInterval(() => void poll(), 1500)
    }
    return () => {
      source?.close()
      if (pollTimer) clearInterval(pollTimer)
    }
  }, [activeCreationRun?.id, activeCreationRun?.run_id, activeCreationRun?.status, systemBrief, systemSessionId])

  useEffect(() => {
    const runId = activeCreationRun?.id || activeCreationRun?.run_id
    const runStatus = activeCreationRun?.status
    if (!runId || !runStatus || !['waiting_user', 'waiting_author', 'completed', 'failed', 'cancelled', 'interrupted'].includes(runStatus)) return
    if (activeCreationRun.card_presentation?.judged_by === 'model') return
    const attemptKey = `${runId}:${activeCreationRun.card_presentation?.judged_by || 'none'}:${selectedModel || 'default'}`
    if (creationRunPresentationAttemptRef.current.has(attemptKey)) return
    creationRunPresentationAttemptRef.current.add(attemptKey)
    const requestId = ++creationRunPresentationRequestRef.current
    const latestAssistantReply = [...messages].reverse().find((item) => item.role === 'assistant')?.content || ''
    const adjudicate = async () => {
      try {
        const response = await apiClient.post<ApiResponse<{ run: NovelCreationRunSummary }>>(
          `/novel-creation/runs/${runId}/card-presentation`,
          { message: latestAssistantReply, model: selectedModel || null },
          { timeout: 0 },
        )
        if (creationRunPresentationRequestRef.current !== requestId) return
        const judgedRun = response.data.data.run
        setActiveCreationRun((current) => (
          current && (current.id || current.run_id) === runId ? judgedRun : current
        ))
        setMessages((previous) => previous.map((item) => (
          item.run && (item.run.id || item.run.run_id) === runId
            ? { ...item, run: judgedRun }
            : item
        )))
      } catch {
        // Keep the raw durable status visible if the selected model is unavailable.
      }
    }
    void adjudicate()
    return () => {
      if (creationRunPresentationRequestRef.current === requestId) {
        creationRunPresentationRequestRef.current += 1
      }
    }
  }, [activeCreationRun?.id, activeCreationRun?.run_id, activeCreationRun?.status, activeCreationRun?.card_presentation?.judged_by, messages, selectedModel])

  // Load creation templates on mount
  useEffect(() => {
    setCreationTemplates(readCreationTemplates())
  }, [])

  const activeProject = useMemo(
    () => projects.find((project) => project.id === activeProjectId),
    [projects, activeProjectId],
  )
  const activeCreationContext = useMemo(
    () => creationSessions.find((session) => session.id === systemSessionId),
    [creationSessions, systemSessionId],
  )
  const assistantContextLabel = activeProject
    ? `作品上下文 · ${activeProject.title}`
    : activeCreationContext
      ? `作品上下文 · ${(activeCreationContext.display_title || activeCreationContext.user_brief || '未命名作品').slice(0, 28)}`
      : '未选择作品 · 发送消息后自动新建'
  const assistantContextOptions = useMemo(() => {
    const projectIds = new Set(projects.map((project) => project.id))
    return [
      ...projects.map((project) => ({ value: `project:${project.id}`, label: project.title })),
      ...creationSessions
        .filter((session) => !session.created_project_id || !projectIds.has(session.created_project_id))
        .map((session) => ({
          value: `creation:${session.id}`,
          label: `${(session.display_title || session.user_brief || '未命名作品').slice(0, 42)} · 筹备中`,
        })),
    ]
  }, [creationSessions, projects])

  useEffect(() => {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, sidebarCollapsed ? '1' : '0')
    localStorage.removeItem(LEGACY_SIDEBAR_STORAGE_KEY)
  }, [sidebarCollapsed])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: motionAwareScrollBehavior() })
  }, [messages])

  const fetchProjects = useCallback(async () => {
    setProjectsLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<{ items: Project[]; total: number }>>('/projects')
      const items = res.data?.data?.items || []
      setProjects(items)

      const savedProjectId = localStorage.getItem(PROJECT_STORAGE_KEY) || localStorage.getItem(LEGACY_PROJECT_STORAGE_KEY) || undefined
      const nextProject = items.find((item) => item.id === savedProjectId)
      if (nextProject && !requestedCreationSession && !requestedConversationId) {
        setActiveProjectId((current) => current || nextProject.id)
        localStorage.setItem(PROJECT_STORAGE_KEY, nextProject.id)
      } else if (savedProjectId) {
        localStorage.removeItem(PROJECT_STORAGE_KEY)
        localStorage.removeItem(LEGACY_PROJECT_STORAGE_KEY)
      }
    } catch (err: any) {
      message.error(err.message || '加载作品失败')
    } finally {
      setProjectsLoading(false)
    }
  }, [requestedConversationId, requestedCreationSession])

  const fetchCreationSessions = useCallback(async () => {
    try {
      const response = await apiClient.get<ApiResponse<{ sessions: CreationSessionContext[] }>>(
        '/novel-creation/sessions',
        { include_completed: true },
      )
      const items = response.data.data.sessions || []
      setCreationSessions(items)
      return items
    } catch {
      setCreationSessions([])
      return []
    }
  }, [])

  const fetchConversations = useCallback(async (projectId = activeProjectId) => {
    setConversationsLoading(true)
    try {
      const responses = projectId
        ? [await apiClient.get<ApiResponse<{ items: Conversation[]; total: number }>>(
            '/ai/system-assistant/conversations',
            { scope_type: 'project', scope_id: projectId },
          )]
        : await Promise.all([
            apiClient.get<ApiResponse<{ items: Conversation[]; total: number }>>(
              '/ai/system-assistant/conversations', { scope_type: 'system' },
            ),
            apiClient.get<ApiResponse<{ items: Conversation[]; total: number }>>(
              '/ai/system-assistant/conversations', { scope_type: 'creation' },
            ),
          ])
      const items = responses
        .flatMap((res) => res.data?.data?.items || [])
        .sort((left, right) => {
          const rightTime = apiDateTimeMs(right.updated_at || right.created_at)
          const leftTime = apiDateTimeMs(left.updated_at || left.created_at)
          return (Number.isFinite(rightTime) ? rightTime : 0) - (Number.isFinite(leftTime) ? leftTime : 0)
        })
      setConversations(items)
      return items
    } catch {
      setConversations([])
      return []
    } finally {
      setConversationsLoading(false)
    }
  }, [activeProjectId])

  const fetchMessages = useCallback(async (convId: string) => {
    setLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<{ conversation: Conversation; messages: PersistedMessage[] }>>(
        `/ai/system-assistant/conversations/${convId}`,
      )
      const loadedMessages = (res.data?.data?.messages || []).map((item) => ({
        id: item.id,
        role: item.role,
        content: item.content || item.payload?.reply || '',
        status: item.status,
        created_at: item.created_at,
        messageType: item.message_type,
        questions: item.payload?.question ? [item.payload.question] : undefined,
        run: item.payload?.run,
      }))
      setMessages(loadedMessages)
      const restoredMessage = [...loadedMessages].reverse().find((item) => item.run)
      const restoredRun = restoredMessage?.run
      setActiveCreationRun(restoredRun || null)
      creationRunMessageRef.current = restoredMessage?.id
        ? { conversationId: convId, assistantMessageId: restoredMessage.id }
        : null
      setActiveConvId(res.data.data.conversation.id)
      setSystemConversationId(res.data.data.conversation.id)
      systemConversationIdRef.current = res.data.data.conversation.id
      if (!activeProjectId) {
        const conversation = res.data.data.conversation
        if (conversation.creation_session_id) {
          adoptCreationSession(conversation.creation_session_id, conversation.user_brief || '')
        } else {
          resetCreationSession()
        }
        setSystemBlueprints(conversation.blueprints || [])
      }
    } catch (err: any) {
      message.error(err.message || '加载对话失败')
    } finally {
      setLoading(false)
    }
  }, [activeProjectId, adoptCreationSession, resetCreationSession])

  useEffect(() => {
    fetchProjects()
    void fetchCreationSessions()
  }, [fetchCreationSessions, fetchProjects])

  useEffect(() => {
    if (projectsLoading) return
    if (!activeProjectId) {
      setActiveConvId(null)
      setSystemConversationId(undefined)
      systemConversationIdRef.current = undefined
      setMessages([])
      localStorage.removeItem(PROJECT_STORAGE_KEY)
      localStorage.removeItem(LEGACY_PROJECT_STORAGE_KEY)
      const pendingCreationSession = pendingCreationContextRef.current
      if (pendingCreationSession) {
        adoptCreationSession(pendingCreationSession)
      }
      fetchConversations(undefined).then((items) => {
        const requested = items.find((item) => item.id === requestedConversationId)
          || items.find((item) => item.creation_session_id === requestedCreationSession)
          || (pendingCreationSession ? items.find((item) => item.creation_session_id === pendingCreationSession) : undefined)
        if (requested) fetchMessages(requested.id)
        pendingCreationContextRef.current = undefined
      })
      return
    }
    localStorage.setItem(PROJECT_STORAGE_KEY, activeProjectId)
    localStorage.removeItem(LEGACY_PROJECT_STORAGE_KEY)
    setActiveConvId(null)
    setSystemConversationId(undefined)
    systemConversationIdRef.current = undefined
    setMessages([])
    void apiClient.post<ApiResponse<{ session: CreationSessionContext }>>(
      `/projects/${activeProjectId}/creation-brief/ensure`,
    ).then((response) => {
      const linked = response.data.data.session
      if (linked) adoptCreationSession(linked.id, linked.user_brief || '')
      else resetCreationSession()
    }).catch(() => {
      resetCreationSession()
      message.warning('作品已打开，但立项资料暂未能同步；可稍后重试')
    })
    fetchConversations(activeProjectId).then((items) => {
      if (items[0]) fetchMessages(items[0].id)
    })
  }, [activeProjectId, adoptCreationSession, fetchConversations, fetchMessages, projectsLoading, requestedConversationId, requestedCreationSession, resetCreationSession])

  const startNewConversation = () => {
    abortRef.current?.abort()
    setStreaming(false)
    setActiveConvId(null)
    setMessages([])
    setInputValue('')
    setSystemConversationId(undefined)
    systemConversationIdRef.current = undefined
    setSystemBlueprints([])
    setPendingFiles([])
    setPendingInputClarification(null)
  }

  const selectAssistantContext = async (value?: string) => {
    startNewConversation()
    if (!value) {
      setActiveProjectId(undefined)
      resetCreationSession()
      return
    }
    const [kind, id] = value.split(':', 2)
    if (kind === 'project') {
      setActiveProjectId(id)
      return
    }
    if (kind === 'creation') {
      if (activeProjectId) {
        pendingCreationContextRef.current = id
        setActiveProjectId(undefined)
        return
      }
      const session = creationSessions.find((item) => item.id === id)
      adoptCreationSession(id, session?.user_brief || '')
      const items = await fetchConversations(undefined)
      const existing = items.find((item) => item.creation_session_id === id)
      if (existing) await fetchMessages(existing.id)
    }
  }

  const deleteConversation = (convId: string) => {
    Modal.confirm({
      title: '删除对话',
      content: '确定要删除这条对话记录吗？删除后无法恢复。',
      okText: '删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await apiClient.delete(`/ai/system-assistant/conversations/${convId}`)
          setConversations((prev) => prev.filter((item) => item.id !== convId))
          if (activeConvId === convId) startNewConversation()
          message.success('对话已删除')
        } catch (err: any) {
          message.error(err.message || '删除对话失败')
        }
      },
    })
  }

  const appendAssistantText = (text: string, replace = false) => {
    setMessages((prev) => {
      const next = [...prev]
      const last = next[next.length - 1]
      if (last?.role === 'assistant') {
        last.content = replace ? text : `${last.content}${text}`
        last.status = 'running'
      }
      return [...next]
    })
  }

  const upsertConversation = (conversation?: Conversation | null) => {
    if (!conversation) return
    setConversations((prev) => {
      const without = prev.filter((item) => item.id !== conversation.id)
      return [conversation, ...without]
    })
  }

  const handleSseEvent = (event: any) => {
    if (event.type === 'conversation') {
      const conversation = event.conversation as Conversation
      setActiveConvId(conversation.id)
      upsertConversation(conversation)
      return
    }
    if (event.type === 'status') {
      appendAssistantText(`\n${event.message || '正在执行...'}\n`, true)
      return
    }
    if (event.type === 'thinking_delta') {
      appendAssistantText(event.delta || '')
      return
    }
    if (event.type === 'thinking') {
      appendAssistantText(event.content || '', true)
      return
    }
    if (event.type === 'tool') {
      const name = event.tool || 'tool'
      const detail = event.detail || event.message || ''
      appendAssistantText(`\n[${name}] ${detail}\n`)
      return
    }
    if (event.type === 'complete') {
      const reply = event.data?.reply?.trim() || EMPTY_ASSISTANT_REPLY
      setMessages((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last?.role === 'assistant') {
          last.content = reply
          last.status = 'completed'
        }
        return [...next]
      })
      upsertConversation(event.data?.conversation)
      return
    }
    if (event.type === 'error') {
      throw new Error(event.message || event.detail || 'AI助手执行失败')
    }
  }

  const setLastAssistantMessage = (content: string, status: ChatMessage['status'] = 'completed') => {
    if (status === 'running') {
      setRunningStartTime(Date.now())
    } else {
      setRunningStartTime(null)
    }
    setMessages((prev) => {
      const next = [...prev]
      const last = next[next.length - 1]
      if (last?.role === 'assistant') {
        last.content = content
        last.status = status
        if (status === 'error' || status === 'aborted') {
          last.questions = undefined
        }
      }
      return [...next]
    })
  }

  const startSystemTurn = async (
    userContent: string,
    state: { creationSessionId?: string; userBrief?: string; messageType?: string },
    forceNewConversation = false,
  ) => {
    let conversationId = forceNewConversation ? undefined : systemConversationIdRef.current
    if (!conversationId) {
      const createRes = await apiClient.post<ApiResponse<{ conversation: Conversation }>>(
        '/ai/system-assistant/conversations',
        {
          title: userContent.slice(0, 36),
          scope_type: state.creationSessionId ? 'creation' : 'system',
          scope_id: state.creationSessionId || null,
        },
      )
      conversationId = createRes.data.data.conversation.id
      setSystemConversationId(conversationId)
      systemConversationIdRef.current = conversationId
    }
    const response = await apiClient.post<ApiResponse<{ conversation: Conversation; messages: ChatMessage[] }>>(
      `/ai/system-assistant/conversations/${conversationId}/turns/start`,
      {
        user_content: userContent,
        creation_session_id: state.creationSessionId || null,
        user_brief: state.userBrief || '',
        message_type: state.messageType || 'text',
        scope_type: state.creationSessionId ? 'creation' : 'system',
        scope_id: state.creationSessionId || null,
      },
    )
    if (!activeProjectId) {
      setActiveConvId(conversationId)
      upsertConversation(response.data.data.conversation)
    }
    return {
      conversationId,
      userMessageId: response.data.data.messages[0]?.id,
      assistantMessageId: response.data.data.messages[1]?.id,
    }
  }

  const finishSystemTurn = async (
    conversationId: string,
    assistantMessageId: string | undefined,
    assistantContent: string,
    status: ChatMessage['status'],
    state: {
      creationSessionId?: string
      userBrief?: string
      blueprints?: NovelBlueprint[]
      messageType?: string
      run?: NovelCreationRunSummary
      question?: ChatQuestion
    },
  ) => {
    if (!assistantMessageId) return
    await apiClient.patch(
      `/ai/system-assistant/conversations/${conversationId}/turns/${assistantMessageId}`,
      {
        assistant_content: assistantContent,
        status: status || 'completed',
        creation_session_id: state.creationSessionId || null,
        user_brief: state.userBrief || '',
        blueprints: state.blueprints || [],
        run_id: state.run?.id || state.run?.run_id || null,
        operation_id: state.run?.operation_id || null,
        message_type: state.messageType || (state.run ? 'operation' : state.question ? 'question' : 'text'),
        scope_type: state.creationSessionId ? 'creation' : 'system',
        scope_id: state.creationSessionId || null,
        payload: state.run ? { run: state.run } : state.question ? { question: state.question } : null,
      },
    )
  }

  const persistSystemTurn = async (
    userContent: string,
    assistantContent: string,
    status: ChatMessage['status'],
    state: {
      creationSessionId?: string
      userBrief?: string
      blueprints?: NovelBlueprint[]
    },
  ) => {
    try {
      let conversationId = systemConversationIdRef.current
      if (!conversationId) {
        const createRes = await apiClient.post<ApiResponse<{ conversation: Conversation }>>(
          '/ai/system-assistant/conversations',
          {
            title: userContent.slice(0, 36),
            scope_type: state.creationSessionId ? 'creation' : 'system',
            scope_id: state.creationSessionId || null,
          },
        )
        conversationId = createRes.data.data.conversation.id
        setSystemConversationId(conversationId)
        systemConversationIdRef.current = conversationId
      }
      const turnRes = await apiClient.post<ApiResponse<{ conversation: Conversation }>>(
        `/ai/system-assistant/conversations/${conversationId}/turns`,
        {
          user_content: userContent,
          assistant_content: assistantContent,
          status: status || 'completed',
          creation_session_id: state.creationSessionId || null,
          user_brief: state.userBrief || '',
          blueprints: state.blueprints || [],
          scope_type: state.creationSessionId ? 'creation' : 'system',
          scope_id: state.creationSessionId || null,
        },
      )
      if (!activeProjectId) {
        setActiveConvId(conversationId)
        upsertConversation(turnRes.data.data.conversation)
      }
    } catch {
      message.warning('本轮回复已完成，但系统对话历史保存失败')
    }
  }

  const handleSystemAssistantMessage = async (
    text: string,
    originalText?: string,
    grantLocalCliOnce = false,
    localCliReadPaths: string[] = [],
    skipAutomaticCreation = false,
    forceGeneralChat = false,
  ) => {
    const sourceText = text
    const displayText = originalText || text
    setAgentRuntimeOverride({})
    const forceFreshCreation = (
      !skipAutomaticCreation && (
        (Boolean(activeProjectId) && shouldUseNovelCreation(displayText, true))
        || (Boolean(systemSessionId) && requestsFreshNovelContext(displayText))
      )
    )
    let finalReply = ''
    let finalStatus: ChatMessage['status'] = 'completed'
    let durableTurn: { conversationId: string; assistantMessageId?: string } | null = null
    let persistedSessionId = systemSessionId
    let persistedBrief = systemBrief
    let persistedBlueprints = systemBlueprints
    let durablePayload: { run?: NovelCreationRunSummary; question?: ChatQuestion } = {}
    const finish = (
      content: string,
      status: ChatMessage['status'] = 'completed',
      payload: { run?: NovelCreationRunSummary; question?: ChatQuestion } = {},
    ) => {
      finalReply = content
      finalStatus = status
      durablePayload = payload
      setLastAssistantMessage(content, status)
    }

    if (forceFreshCreation) {
      abortRef.current?.abort()
      setActiveProjectId(undefined)
      resetCreationSession()
      persistedSessionId = undefined
      persistedBrief = ''
      persistedBlueprints = []
      setSystemConversationId(undefined)
      systemConversationIdRef.current = undefined
      creationArtifactRequestRef.current += 1
      setCreationArtifacts([])
      setCreationConsistency(null)
      setActiveCreationRun(null)
    }

    setMessages((prev) => [
      ...(forceFreshCreation ? [] : prev),
      { role: 'user', content: displayText, status: 'completed', created_at: new Date().toISOString() },
      { role: 'assistant', content: '正在处理...', status: 'running', created_at: new Date().toISOString() },
    ])
    setInputValue('')
    setStreaming(true)

    if (!skipAutomaticCreation && !persistedSessionId && (!activeProjectId || forceFreshCreation)) {
      try {
        const created = await startNovelCreationSession({
          userBrief: displayText,
          creationMode: 'author_led',
          authorBrief: displayText.slice(0, 5000),
        })
        persistedSessionId = created.id
        persistedBrief = displayText
        adoptCreationSession(created.id, displayText)
        await fetchCreationSessions()
      } catch (error: unknown) {
        finish(error instanceof Error ? error.message : '新建立项数据失败，请重试。', 'error')
        setStreaming(false)
        return
      }
    }

    try {
      durableTurn = await startSystemTurn(displayText, {
        creationSessionId: persistedSessionId,
        userBrief: persistedBrief,
      }, forceFreshCreation)
    } catch {
      message.warning('本轮消息暂未保存到系统历史，仍会继续处理请求')
    }

    try {
      if (forceGeneralChat) {
        try {
          const chatRes = await apiClient.post<ApiResponse<{ reply: string }>>('/novel-creation/system-chat', {
            message: sourceText,
            model: selectedModel,
            context: {
              blueprints: systemBlueprints,
              sessionId: systemSessionId,
              brief: systemBrief,
              history: messages.slice(-8).map((item) => ({ role: item.role, content: item.content })),
              readOnly: true,
            },
          }, { timeout: 0 })
          const reply = String(chatRes.data?.data?.reply || '').trim()
          if (!reply) throw new Error('当前模型没有返回文字回复。')
          finish(reply)
        } catch (error: unknown) {
          finish(formatSystemAssistantError(error), 'error')
        }
        return
      }
      const controlText = displayText.trim().replace(/[。！!？?\s]+/g, '')
      const controlAction = /^(停止|取消)$/.test(controlText)
        ? 'cancel'
        : /^(暂停)$/.test(controlText)
          ? 'pause'
          : /^(继续|恢复|继续生成)$/.test(controlText)
            ? 'continue'
            : /^(按原输入重试)$/.test(controlText)
              ? 'retry-original'
              : /^(重试|按最新内容重试|切换模型后重试|用当前模型重试)$/.test(controlText)
                ? 'retry-latest'
              : null
      if (controlAction && activeCreationRun?.operation_id) {
        if (controlAction === 'retry-original' || controlAction === 'retry-latest') {
          const runId = activeCreationRun.id || activeCreationRun.run_id
          if (!runId) throw new Error('缺少可重试的立项任务')
          const useLatestDraft = controlAction === 'retry-latest'
          const response = await apiClient.post<ApiResponse<{ run: NovelCreationRunSummary }>>(
            `/novel-creation/runs/${runId}/retry`,
            { use_latest_draft: useLatestDraft, model: selectedModel || null },
          )
          setActiveCreationRun(response.data.data.run)
          finish(
            `已按${useLatestDraft ? '最新内容' : '原输入'}和当前模型创建重试任务。`,
            'running',
            { run: response.data.data.run },
          )
        } else {
          const endpoint = controlAction === 'continue' ? 'continue' : controlAction
          const response = await apiClient.post<ApiResponse<NovelCreationRunSummary>>(
            `/operations/${activeCreationRun.operation_id}/${endpoint}`,
          )
          const nextStatus = controlAction === 'pause' ? 'paused' : controlAction === 'continue' ? 'running' : 'cancelled'
          const nextRun = { ...activeCreationRun, status: nextStatus }
          setActiveCreationRun(nextRun)
          finish(
            controlAction === 'pause' ? '任务已暂停，检查点和已有草稿均已保留。'
              : controlAction === 'continue' ? '正在从最近检查点继续。'
                : '任务已取消，已有草稿保持不变。',
            controlAction === 'continue' ? 'running' : 'completed',
            { run: nextRun },
          )
          void response
        }
        return
      }

      if (persistedSessionId) {
        const response = await apiClient.post<ApiResponse<{
          reply: string
          run?: NovelCreationRunSummary | null
          tool_results?: Array<Record<string, unknown>>
        }>>('/novel-creation/agent-turn', {
          session_id: persistedSessionId,
          message: sourceText,
          model: selectedModel || null,
          history: messages.slice(-12).map((item) => ({ role: item.role, content: item.content })),
          local_cli_permission_grant: grantLocalCliOnce ? 'creation_agent_once' : 'chat_only',
          local_cli_read_permission_grant: localCliReadPaths.length > 0 ? 'read_once' : 'none',
          local_cli_read_paths: localCliReadPaths,
        })
        const run = response.data.data.run
        if (run) setActiveCreationRun(run)
        await Promise.all([fetchCreationArtifacts(), fetchCreationSessions()])
        finish(
          response.data.data.reply || run?.current_message || '已完成本轮立项工具调用',
          run ? 'running' : 'completed',
          run ? { run } : undefined,
        )
        return
      }

      if (/作品|项目|列表|有哪些|查看/.test(text)) {
        const res = await apiClient.get<ApiResponse<{ items: Project[]; total: number }>>('/projects')
        const items = res.data?.data?.items || []
        setProjects(items)
        if (!items.length) {
          finish('当前还没有作品。你可以直接说“我想写克苏鲁+修仙+规则怪谈”，我会先生成一套创意方向，再按你的反馈持续调整。')
          return
        }
        const projectList = items.map((project, index) => `${index + 1}. ${project.title}`).join('\n')
        finish(`当前共有 ${items.length} 个作品：\n${projectList}\n\n你可以在顶部作品下拉框切换作品，也可以直接让我创建新作品。`)
        return
      }

      // LLM-powered natural conversation fallback
      try {
        const chatRes = await apiClient.post<ApiResponse<{ reply: string }>>('/novel-creation/system-chat', {
          message: sourceText,
          model: selectedModel,
          context: {
            blueprints: systemBlueprints,
            sessionId: systemSessionId,
            brief: systemBrief,
            importedFiles: pendingFiles.map(f => ({ name: f.name, length: f.content.length })),
            history: messages.slice(-6).map(m => ({ role: m.role, content: m.content })),
          },
        }, { timeout: 0 })
        const reply = String(chatRes.data?.data?.reply || '').trim()
        if (!reply) {
          throw new Error('当前模型没有返回文字回复。请重试，或在系统设置中测试当前模型/CLI。')
        }
        finish(reply)
      } catch (err: unknown) {
        recordAgentRuntimeError(err)
        finish(formatSystemAssistantError(err), 'error')
      }
    } catch (err: any) {
      recordAgentRuntimeError(err)
      finish(err.message || '处理失败', 'error')
      message.error(err.message || '处理失败')
    } finally {
      if (durableTurn) {
        try {
          await finishSystemTurn(durableTurn.conversationId, durableTurn.assistantMessageId, finalReply, finalStatus, {
            creationSessionId: persistedSessionId,
            userBrief: persistedBrief,
            blueprints: persistedBlueprints,
            run: durablePayload.run,
            question: durablePayload.question,
          })
          if (durablePayload.run && durableTurn.assistantMessageId) {
            creationRunMessageRef.current = {
              conversationId: durableTurn.conversationId,
              assistantMessageId: durableTurn.assistantMessageId,
            }
          }
        } catch {
          message.warning('系统对话状态更新失败，可稍后刷新历史重试')
        }
      } else if (finalReply) {
        await persistSystemTurn(displayText, finalReply, finalStatus, {
          creationSessionId: persistedSessionId,
          userBrief: persistedBrief,
          blueprints: persistedBlueprints,
        })
      }
      setStreaming(false)
    }
  }

  const handleFileImport = async (file: File) => {
    try {
      const extension = file.name.split('.').pop()?.toLowerCase()
      if (!extension || !['txt', 'md', 'docx', 'json'].includes(extension)) {
        throw new Error('仅支持 txt、md、docx 和 json 文件')
      }
      if (file.size > 25 * 1024 * 1024) throw new Error('文件超过 25MB 上限')
      let text = ''
      if (extension !== 'docx') {
        text = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(String(reader.result || ''))
          reader.onerror = () => reject(new Error('文件读取失败'))
          reader.readAsText(file, 'utf-8')
        })
      }
      setPendingInputClarification(null)
      setPendingFiles([{ name: file.name, size: file.size, file, content: text }])
      message.success(`已添加「${file.name}」（${Math.max(1, Math.round(file.size / 1024))} KB）`)
    } catch {
      message.error('文件读取失败，请确认格式和大小后重试。')
    }
  }

  const handleMaterialImport = async (
    pending: PendingMaterialFile,
    userText: string,
    displayInstruction?: string,
  ) => {
    const sourceText = pending.content
    const pastedLongText = pending.name === '聊天长文本.txt'
    const displayText = displayInstruction || (pastedLongText
      ? `已提交长文本（${sourceText.length.toLocaleString('zh-CN')} 字）\n${sourceText.slice(0, 240)}${sourceText.length > 240 ? '……' : ''}`
      : (userText || `📎 ${pending.name}`))
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: displayText, status: 'completed', created_at: new Date().toISOString() },
      { role: 'assistant', content: '正在保存原始文件并创建持久导入任务…', status: 'running', created_at: new Date().toISOString() },
    ])
    setInputValue('')
    setStreaming(true)
    let sessionId = systemSessionId
    let durableTurn: Awaited<ReturnType<typeof startSystemTurn>> | null = null
    try {
      if (!sessionId) {
        const creationSeed = sourceText.length > 5000
          ? `用户提交了 ${sourceText.length.toLocaleString('zh-CN')} 字作品资料，原文已进入持久化分块导入任务。`
          : (userText || sourceText)
        const created = await startNovelCreationSession({
          userBrief: creationSeed,
          creationMode: 'author_led',
          authorBrief: creationSeed,
        })
        sessionId = created.id
        adoptCreationSession(created.id, creationSeed)
      }
      try {
        durableTurn = await startSystemTurn(displayText, {
          creationSessionId: sessionId,
          userBrief: sourceText.length > 5000 ? `已导入 ${sourceText.length.toLocaleString('zh-CN')} 字长文本` : sourceText,
          messageType: 'operation',
        })
      } catch {
        message.warning('对话消息暂未绑定，但文件导入任务仍会持久保存')
      }
      const form = new FormData()
      form.append('file', pending.file, pending.name)
      if (selectedModel) form.append('model', selectedModel)
      if (durableTurn?.userMessageId) form.append('source_message_id', durableTurn.userMessageId)
      const response = await apiClient.postForm<ApiResponse<MaterialImportSummary>>(
        `/novel-creation/sessions/${sessionId}/imports`,
        form,
        { timeout: 0 },
      )
      const importRun = response.data.data
      setActiveMaterialImport(importRun)
      setPendingInputClarification(null)
      setPendingFiles([])
      setLastAssistantMessage(`已保存《${pending.name}》，正在按分块整理人物、地点、势力、卷纲和章节摘要。关闭页面不会取消任务。`, 'completed')
      if (durableTurn) {
        await finishSystemTurn(
          durableTurn.conversationId,
          durableTurn.assistantMessageId,
          `已保存《${pending.name}》，正在生成可恢复的导入预览。`,
          'completed',
          {
            creationSessionId: sessionId,
            userBrief: displayText,
            messageType: 'operation',
            run: {
              id: importRun.id,
              session_id: sessionId,
              stage: 'material_import',
              status: importRun.status,
              operation_id: importRun.operation_id,
            },
          },
        )
      }
      await fetchCreationArtifacts()
    } catch (error: unknown) {
      const detail = error instanceof Error ? error.message : '资料导入任务创建失败'
      setLastAssistantMessage(`导入未开始：${detail}。原有立项数据没有变化。`, 'error')
    } finally {
      setStreaming(false)
    }
  }

  const applyMaterialImportPreview = async () => {
    if (!activeMaterialImport || selectedImportArtifacts.length === 0) return
    setImportActionLoading(true)
    try {
      const revision = creationArtifacts[0]?.revision ?? activeMaterialImport.input_revision
      const response = await apiClient.post<ApiResponse<MaterialImportSummary['result']>>(
        `/novel-creation/imports/${activeMaterialImport.id}/apply`,
        {
          selected_artifacts: selectedImportArtifacts,
          strategy: importStrategy,
          expected_revision: revision,
        },
      )
      const result = response.data.data || {}
      setActiveMaterialImport((previous) => previous ? { ...previous, status: 'completed', result } : previous)
      setImportPreviewOpen(false)
      await fetchCreationArtifacts()
      const applied = (result.applied || []).map((item) => `${item.artifact}（${item.count}）`).join('、') || '无'
      const skipped = result.skipped?.length ? `；跳过 ${result.skipped.length} 项冲突` : ''
      setMessages((previous) => [
        ...previous,
        { role: 'assistant', content: `导入已完成：${applied}${skipped}。所有写入均保留文件、分块和导入运行来源。`, status: 'completed', created_at: new Date().toISOString() },
      ])
      message.success('所选资料已写入立项数据')
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : '应用导入预览失败；原有数据未改变')
    } finally {
      setImportActionLoading(false)
    }
  }

  const retryMaterialImport = async () => {
    if (!activeMaterialImport) return
    setImportActionLoading(true)
    try {
      const response = await apiClient.post<ApiResponse<MaterialImportSummary>>(
        `/novel-creation/imports/${activeMaterialImport.id}/retry`,
        null,
        { params: selectedModel ? { model: selectedModel } : undefined },
      )
      setActiveMaterialImport(response.data.data)
      message.success('已从上一个分块检查点继续导入')
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : '重试失败')
    } finally {
      setImportActionLoading(false)
    }
  }

  const removePendingFile = (index: number) => {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index))
  }

  const renderPendingFiles = () => {
    if (pendingFiles.length === 0) return null
    return (
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, padding: '4px 0' }}>
        {pendingFiles.map((f, i) => (
          <Tag
            key={i}
            closable
            onClose={() => removePendingFile(i)}
            color="blue"
          >
            📎 {f.name} ({Math.max(1, Math.round(f.size / 1024))} KB)
          </Tag>
        ))}
      </div>
    )
  }

  // Import file directly as a new project (skip novel creation flow)
  const handleFileImportAsProject = async (file: PendingMaterialFile, userText: string) => {
    const displayText = userText || `📎 ${file.name}`
    setMessages((prev) => [...prev, { role: 'user', content: displayText }])
    setMessages((prev) => [...prev, { role: 'assistant', content: '正在创建作品并导入文件...', status: 'running' }])
    setStreaming(true)
    setInputValue('')

    try {
      // Create a new project
      const title = file.name.replace(/\.(txt|docx)$/i, '')
      const createRes = await apiClient.post<ApiResponse<{ project_id: string }>>('/projects', {
        title: title,
        description: `从文件「${file.name}」导入`,
        tags: '导入',
      })
      const projectId = createRes.data.data.project_id

      let parsedText = file.content
      if (!parsedText.trim()) {
        const uploadForm = new FormData()
        uploadForm.append('file', file.file, file.name)
        const uploadRes = await apiClient.postForm<ApiResponse<{ text: string }>>(
          `/projects/${projectId}/import/file`,
          uploadForm,
          { timeout: 0 },
        )
        parsedText = String(uploadRes.data.data.text || '')
      }
      if (!parsedText.trim()) throw new Error('文件内容为空或无法解析')
      let splits: Array<Record<string, unknown>> = []
      if (parsedText.length >= 100) {
        try {
          const previewRes = await apiClient.post<ApiResponse<{ splits: Array<Record<string, unknown>> }>>(
            `/projects/${projectId}/import/preview`,
            { text: parsedText, model: selectedModel || null },
            { timeout: 0 },
          )
          splits = previewRes.data.data.splits || []
        } catch {
          // Confirm safely falls back when chapter-boundary detection fails.
        }
      }
      const confirmRes = await apiClient.post<ApiResponse<{ total: number }>>(
        `/projects/${projectId}/import/confirm`,
        { text: parsedText, splits },
        { timeout: 0 },
      )
      const chapterCount = Number(confirmRes.data.data.total || 0)

      await fetchProjects()
      setPendingInputClarification(null)
      setPendingFiles([])
      setActiveProjectId(projectId)
      localStorage.setItem(PROJECT_STORAGE_KEY, projectId)

      setMessages((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last?.role === 'assistant' && last?.status === 'running') {
          last.content = `已创建作品「${title}」并导入 ${chapterCount} 章（${parsedText.length}字）。已切换到该作品上下文，可以继续编辑。`
          last.status = 'completed'
        }
        return [...next]
      })
      setStreaming(false)
    } catch (error) {
      setMessages((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last?.role === 'assistant' && last?.status === 'running') {
          last.content = error instanceof Error ? `导入失败：${error.message}` : '导入失败，请重试。'
          last.status = 'error'
        }
        return [...next]
      })
      setStreaming(false)
    }
  }

  const classifyDataInput = async (
    source: PendingMaterialFile,
    sourceKind: 'long_text' | 'attachment',
    userInstruction: string,
    clarificationHistory: Array<{ question: string; answer: string }> = [],
  ) => {
    const latestClarification = clarificationHistory[clarificationHistory.length - 1]
    const sharedFields = {
      user_instruction: userInstruction,
      clarification_question: latestClarification?.question || '',
      clarification_answer: latestClarification?.answer || '',
      clarification_already_asked: clarificationHistory.length > 0,
      clarification_history: clarificationHistory,
      context_scope: activeProjectId ? 'project' : systemSessionId ? 'creation' : 'system',
      active_project_id: activeProjectId || '',
      creation_session_id: systemSessionId || '',
      history: messages.slice(-8).map((item) => ({ role: item.role, content: item.content })),
      model: selectedModel || null,
    }
    if (sourceKind === 'attachment') {
      const form = new FormData()
      form.append('file', source.file, source.name)
      Object.entries(sharedFields).forEach(([key, value]) => {
        form.append(
          key,
          (key === 'history' || key === 'clarification_history')
            ? JSON.stringify(value)
            : String(value ?? ''),
        )
      })
      const response = await apiClient.postForm<ApiResponse<AssistantInputRouteDecision>>(
        '/novel-creation/assistant-input/route-file',
        form,
        { timeout: 0 },
      )
      return response.data.data
    }
    const response = await apiClient.post<ApiResponse<AssistantInputRouteDecision>>(
      '/novel-creation/assistant-input/route',
      {
        source_name: source.name,
        source_text: source.content,
        source_kind: sourceKind,
        ...sharedFields,
      },
      { timeout: 0 },
    )
    return response.data.data
  }

  const askHowToHandleData = async (
    pending: Omit<PendingInputClarification, 'currentQuestion'>,
    question: string,
    answer?: string,
  ) => {
    const displayText = answer || (pending.sourceKind === 'long_text'
      ? `已提交长文本（${pending.source.content.length.toLocaleString('zh-CN')} 字）\n${pending.source.content.slice(0, 240)}${pending.source.content.length > 240 ? '……' : ''}`
      : (pending.originalInstruction || `📎 ${pending.source.name}`))
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: displayText, status: 'completed', created_at: new Date().toISOString() },
      { role: 'assistant', content: question, status: 'completed', created_at: new Date().toISOString() },
    ])
    setPendingInputClarification({ ...pending, currentQuestion: question })
    setPendingFiles([])
    setInputValue('')
    try {
      await persistSystemTurn(displayText, question, 'completed', {
        creationSessionId: systemSessionId,
        userBrief: systemBrief,
        blueprints: systemBlueprints,
      })
    } catch {
      // The pending in-memory source remains available even when history persistence fails.
    }
  }

  const buildReferenceMessage = (
    decision: AssistantInputRouteDecision,
    source: PendingMaterialFile,
    originalInstruction: string,
    clarificationAnswer?: string,
  ) => {
    const instruction = decision.resolved_instruction
      || clarificationAnswer
      || originalInstruction
      || '请结合这份内容提供最有帮助的分析'
    const context = decision.source_context || source.content.slice(0, 16_000)
    const sourceChars = decision.source_coverage?.source_chars ?? source.content.length
    const coverage = decision.source_coverage?.coverage === 'distributed'
      ? `以下是覆盖原文开头、中段与结尾的路由视图；原文共 ${sourceChars.toLocaleString('zh-CN')} 字。若完成任务必须逐字读取全文，请明确告知用户需要进入持久化资料流程。`
      : '以下包含本次提交的完整数据内容。'
    return [
      `[提交数据：${source.name}]`,
      coverage,
      context,
      '',
      `用户处理要求：${instruction}`,
    ].join('\n\n')
  }

  const executeDataInputDecision = async (
    decision: AssistantInputRouteDecision,
    source: PendingMaterialFile,
    sourceKind: 'long_text' | 'attachment',
    originalInstruction: string,
    clarificationHistory: Array<{ question: string; answer: string }> = [],
    clarificationAnswer?: string,
    grantLocalCliOnce = false,
    readPaths: string[] = [],
  ) => {
    const resolvedInstruction = decision.resolved_instruction
      || clarificationAnswer
      || originalInstruction

    if (decision.route === 'clarify') {
      await askHowToHandleData(
        { source, sourceKind, originalInstruction, exchanges: clarificationHistory },
        decision.clarification_question || '你希望我怎样处理这份内容？',
        clarificationAnswer,
      )
      return
    }
    if (decision.route === 'creation_material') {
      setPendingInputClarification(null)
      await handleMaterialImport(source, resolvedInstruction, clarificationAnswer || originalInstruction || undefined)
      return
    }
    if (decision.route === 'new_project_import') {
      setPendingInputClarification(null)
      await handleFileImportAsProject(source, resolvedInstruction || originalInstruction)
      setPendingFiles([])
      return
    }

    const messageForAssistant = decision.route === 'chat_only'
      ? (resolvedInstruction || clarificationAnswer || originalInstruction)
      : buildReferenceMessage(decision, source, originalInstruction, clarificationAnswer)
    const displayText = clarificationAnswer
      || originalInstruction
      || (sourceKind === 'attachment' ? `📎 ${source.name}` : `已提交长文本（${source.content.length.toLocaleString('zh-CN')} 字）`)
    setPendingFiles([])
    setPendingInputClarification(null)
    if (!activeProjectId) {
      await handleSystemAssistantMessage(
        messageForAssistant,
        displayText,
        grantLocalCliOnce,
        readPaths,
        true,
        true,
      )
      return
    }
    await sendMessage({
      grantLocalCliOnce,
      readPaths,
      routedMessage: messageForAssistant,
      routedDisplayText: displayText,
      bypassDataRouting: true,
      projectAutoApply: false,
    })
  }

  const sendMessage = async (options?: {
    grantLocalCliOnce?: boolean
    readPaths?: string[]
    routedMessage?: string
    routedDisplayText?: string
    bypassDataRouting?: boolean
    projectAutoApply?: boolean
  }) => {
    const rawInput = inputValue.trim()
    const text = options?.routedDisplayText ?? rawInput
    const dataSourceInstruction = rawInput
    const inputIsLongData = !options?.bypassDataRouting
      && pendingFiles.length === 0
      && rawInput.length > LONG_CREATION_TEXT_THRESHOLD
    const permissionInstruction = inputIsLongData ? '' : dataSourceInstruction
    // Allow sending if there are pending files (even without text)
    if ((!text && pendingFiles.length === 0 && !pendingInputClarification) || streaming) return
    const effectiveText = options?.routedMessage ?? text
    const grantLocalCliOnce = isLocalCliModel && Boolean(
      options?.grantLocalCliOnce || nextLocalCliGrant,
    )
    const proposedReadPaths = supportsTransientCreationMcp
      ? (options?.readPaths ?? extractExplicitLocalPaths(permissionInstruction))
      : []
    if (
      supportsTransientCreationMcp
      && options?.readPaths === undefined
      && proposedReadPaths.length > 0
    ) {
      Modal.confirm({
        title: '仅允许 OpenCode 读取这些路径一次？',
        okText: '创建只读快照并发送',
        cancelText: '取消',
        content: (
          <div className="gui-chat-cli-grant-copy">
            <p>路径文字本身不会被当作授权。确认后，司命才会为这一条消息创建受限的只读快照：</p>
            <ul>
              {proposedReadPaths.map((path) => <li key={path}><code>{path}</code></li>)}
            </ul>
            <p>OpenCode 只能读取隔离副本，不能访问原路径、父目录或相邻文件；不会获得 Shell 或写文件权限。</p>
            <p>密钥、凭据、网络路径、符号链接及过大的目录会被后端拒绝；本轮结束后快照自动删除。</p>
          </div>
        ),
        onOk: () => void sendMessage({ ...options, readPaths: proposedReadPaths }),
      })
      return
    }
    const grantedReadPaths = supportsTransientCreationMcp ? proposedReadPaths : []
    if (
      isLocalCliModel
      && !options?.bypassDataRouting
      && pendingFiles.length === 0
      && !pendingInputClarification
      && systemSessionId
      && !grantLocalCliOnce
      && LOCAL_CLI_WRITE_INTENT.test(permissionInstruction)
    ) {
      Modal.confirm({
        title: '允许本机 CLI 仅修改本轮立项数据？',
        okText: '允许本轮修改',
        cancelText: '保持只读',
        content: (
          <div className="gui-chat-cli-grant-copy">
            <p>CLI 本身仍处于隔离环境，不能读取项目目录、Shell 或其他程序的配置文件。</p>
            <p>{supportsTransientCreationMcp
              ? '司命会向当前 OpenCode 子进程注入仅含本立项会话工具的临时 MCP；OpenCode 将直接调用并回读验证。'
              : '司命会通过受控工具桥执行立项白名单操作，并校验当前会话与数据版本。'}</p>
            <p>授权在这条消息结束后自动失效，不会扫描或修改该 CLI 的全局 MCP 配置。</p>
          </div>
        ),
        onOk: () => void sendMessage({
          ...options,
          grantLocalCliOnce: true,
          readPaths: grantedReadPaths,
        }),
      })
      return
    }
    setNextLocalCliGrant(false)

    if (!options?.bypassDataRouting && pendingInputClarification) {
      const answer = dataSourceInstruction
      if (!answer) return
      const clarificationHistory = [
        ...pendingInputClarification.exchanges,
        { question: pendingInputClarification.currentQuestion, answer },
      ]
      try {
        setStreaming(true)
        const decision = await classifyDataInput(
          pendingInputClarification.source,
          pendingInputClarification.sourceKind,
          pendingInputClarification.originalInstruction,
          clarificationHistory,
        )
        setStreaming(false)
        if (decision.route !== 'clarify') setPendingInputClarification(null)
        await executeDataInputDecision(
          decision,
          pendingInputClarification.source,
          pendingInputClarification.sourceKind,
          pendingInputClarification.originalInstruction,
          clarificationHistory,
          answer,
          grantLocalCliOnce,
          grantedReadPaths,
        )
      } catch (error) {
        setStreaming(false)
        message.error(error instanceof Error ? error.message : '暂时无法判断处理方式，请重试')
      }
      return
    }

    const isLongDataText = inputIsLongData
    const submittedData = !options?.bypassDataRouting
      ? (pendingFiles[0] || (isLongDataText
        ? (() => {
          const file = new File([effectiveText], '聊天长文本.txt', { type: 'text/plain;charset=utf-8' })
          return { name: file.name, size: file.size, file, content: effectiveText }
        })()
        : undefined))
      : undefined
    if (submittedData) {
      const sourceKind = pendingFiles.length > 0 ? 'attachment' : 'long_text'
      const outerInstruction = sourceKind === 'attachment' ? dataSourceInstruction : ''
      try {
        setStreaming(true)
        const decision = await classifyDataInput(submittedData, sourceKind, outerInstruction)
        setStreaming(false)
        await executeDataInputDecision(
          decision,
          submittedData,
          sourceKind,
          outerInstruction,
          [],
          undefined,
          grantLocalCliOnce,
          grantedReadPaths,
        )
      } catch (error) {
        setStreaming(false)
        message.error(error instanceof Error ? error.message : '暂时无法判断处理方式，请重试')
      }
      return
    }

    let messageWithContext = effectiveText
    const displayText = text

    const continuesSystemCreation = systemBlueprints.length > 0 && !/(写|续写|重写|查看|打开).{0,8}第?\d+\s*章/.test(text)
    if (!activeProjectId || shouldUseNovelCreation(text, Boolean(activeProjectId)) || continuesSystemCreation) {
      await handleSystemAssistantMessage(
        messageWithContext,
        displayText,
        grantLocalCliOnce,
        grantedReadPaths,
      )
      return
    }

    const history = messages.slice(-8).map((item) => ({
      role: item.role,
      content: item.content,
    }))

    setMessages((prev) => [
      ...prev,
      { role: 'user', content: displayText, status: 'completed', created_at: new Date().toISOString() },
      { role: 'assistant', content: '正在分析需求...', status: 'running', created_at: new Date().toISOString() },
    ])
    setInputValue('')
    setStreaming(true)

    let durableProjectTurn: { conversationId: string; assistantMessageId?: string } | null = null
    let durableProjectReply = ''
    let durableProjectRun: NovelCreationRunSummary | undefined
    const normalizeProjectSseEvent = (event: any) => {
      if (!durableProjectTurn) return event
      const canonicalConversation = (conversation?: Conversation) => ({
        ...(conversation || {}),
        id: durableProjectTurn!.conversationId,
        scope_type: 'project' as const,
        scope_id: activeProjectId,
        project_id: activeProjectId,
      })
      if (event.type === 'conversation') {
        return { ...event, conversation: canonicalConversation(event.conversation) }
      }
      if (event.type === 'complete' && event.data) {
        return {
          ...event,
          data: { ...event.data, conversation: canonicalConversation(event.data.conversation) },
        }
      }
      return event
    }
    try {
      let conversationId = systemConversationId || activeConvId || undefined
      if (!conversationId) {
        const createRes = await apiClient.post<ApiResponse<{ conversation: Conversation }>>(
          '/ai/system-assistant/conversations',
          { title: displayText.slice(0, 36), scope_type: 'project', scope_id: activeProjectId },
        )
        conversationId = createRes.data.data.conversation.id
        setSystemConversationId(conversationId)
        setActiveConvId(conversationId)
        upsertConversation(createRes.data.data.conversation)
      }
      const turnRes = await apiClient.post<ApiResponse<{ conversation: Conversation; messages: PersistedMessage[] }>>(
        `/ai/system-assistant/conversations/${conversationId}/turns/start`,
        {
          user_content: displayText,
          message_type: 'text',
          scope_type: 'project',
          scope_id: activeProjectId,
        },
      )
      durableProjectTurn = {
        conversationId,
        assistantMessageId: turnRes.data.data.messages[1]?.id,
      }
      upsertConversation(turnRes.data.data.conversation)

      abortRef.current = new AbortController()
      const res = await fetch(`/api/v1/projects/${activeProjectId}/ai/workspace-assistant/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scope: 'project',
          message: messageWithContext,
          conversation_id: undefined,
          canonical_conversation_id: durableProjectTurn.conversationId,
          creation_session_id: systemSessionId,
          model: selectedModel,
          assistant_mode: 'fast',
          temperature: 0.3,
          max_tokens: undefined,
          auto_apply: options?.projectAutoApply ?? true,
          local_cli_permission_grant: grantLocalCliOnce ? 'project_agent_once' : 'chat_only',
          local_cli_read_permission_grant: grantedReadPaths.length > 0 ? 'read_once' : 'none',
          local_cli_read_paths: grantedReadPaths,
          outline_batch_count: 3,
          history,
        }),
        signal: abortRef.current.signal,
      })

      if (!res.ok || !res.body) throw new Error(`请求失败：${res.status}`)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const frames = buffer.split(/\r?\n\r?\n/)
        buffer = frames.pop() || ''
        for (const frame of frames) {
          const data = frame
            .split(/\r?\n/)
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.replace(/^data:\s?/, ''))
            .join('\n')
          if (!data || data === '[DONE]') continue
          const event = normalizeProjectSseEvent(JSON.parse(data))
          if (event.type === 'run' && event.run?.operation_id) {
            activeProjectOperationRef.current = event.run.operation_id
            durableProjectRun = event.run
          }
          if (event.type === 'complete') {
            durableProjectReply = event.data?.reply?.trim() || EMPTY_ASSISTANT_REPLY
            durableProjectRun = event.data?.run
          }
          handleSseEvent(event)
        }
      }

      buffer += decoder.decode()
      if (buffer.trim()) {
        const data = buffer
          .split(/\r?\n/)
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.replace(/^data:\s?/, ''))
          .join('\n')
        if (data && data !== '[DONE]') {
          const event = normalizeProjectSseEvent(JSON.parse(data))
          if (event.type === 'run' && event.run?.operation_id) {
            activeProjectOperationRef.current = event.run.operation_id
            durableProjectRun = event.run
          }
          if (event.type === 'complete') {
            durableProjectReply = event.data?.reply?.trim() || EMPTY_ASSISTANT_REPLY
            durableProjectRun = event.data?.run
          }
          handleSseEvent(event)
        }
      }
      if (durableProjectTurn?.assistantMessageId) {
        await apiClient.patch(
          `/ai/system-assistant/conversations/${durableProjectTurn.conversationId}/turns/${durableProjectTurn.assistantMessageId}`,
          {
            assistant_content: durableProjectReply || EMPTY_ASSISTANT_REPLY,
            status: 'completed',
            message_type: durableProjectRun ? 'operation' : 'text',
            run_id: durableProjectRun?.id || durableProjectRun?.run_id || null,
            operation_id: durableProjectRun?.operation_id || null,
            payload: durableProjectRun ? { run: durableProjectRun } : null,
            scope_type: 'project',
            scope_id: activeProjectId,
          },
        )
      }
      fetchConversations(activeProjectId)
    } catch (err: any) {
      if (durableProjectTurn?.assistantMessageId) {
        const cancelled = err.name === 'AbortError'
        try {
          await apiClient.patch(
            `/ai/system-assistant/conversations/${durableProjectTurn.conversationId}/turns/${durableProjectTurn.assistantMessageId}`,
            {
              assistant_content: cancelled ? '任务已取消，未完成内容不会写入。' : (err.message || '项目助手执行失败'),
              status: cancelled ? 'cancelled' : 'error',
              message_type: cancelled ? 'operation' : 'error',
              scope_type: 'project',
              scope_id: activeProjectId,
            },
          )
        } catch {
          message.warning('项目助手结果已返回，但统一会话状态保存失败')
        }
      }
      if (err.name !== 'AbortError') {
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last?.role === 'assistant') {
            last.content = err.message || '发送失败'
            last.status = 'error'
          }
          return [...next]
        })
        message.error(err.message || '发送失败')
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
      activeProjectOperationRef.current = null
      projectStopRequestedRef.current = false
    }
  }

  const cancelCreationRun = async () => {
    if (!activeCreationRun?.operation_id || creationRunAction) return
    setCreationRunAction('cancel')
    try {
      await apiClient.post(`/operations/${activeCreationRun.operation_id}/cancel`)
      setActiveCreationRun((current) => current ? {
        ...current,
        status: 'cancelled',
        current_message: '任务已取消；已保存草稿保持不变。',
      } : current)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '取消任务失败')
    } finally {
      setCreationRunAction(null)
    }
  }

  const retryCreationRun = async (useLatestDraft: boolean) => {
    const runId = activeCreationRun?.id || activeCreationRun?.run_id
    if (!runId || creationRunAction) return
    setCreationRunAction(useLatestDraft ? 'retry-latest' : 'retry-original')
    try {
      const response = await apiClient.post<ApiResponse<{ run: NovelCreationRunSummary }>>(
        `/novel-creation/runs/${runId}/retry`,
        { use_latest_draft: useLatestDraft, model: selectedModel || null },
      )
      setActiveCreationRun(response.data.data.run)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '重试任务失败')
    } finally {
      setCreationRunAction(null)
    }
  }

  const pauseCreationRun = async () => {
    if (!activeCreationRun?.operation_id || creationRunAction) return
    setCreationRunAction('pause')
    try {
      await apiClient.post(`/operations/${activeCreationRun.operation_id}/pause`)
      setActiveCreationRun((current) => current ? { ...current, status: 'paused', current_message: '任务已暂停；检查点和已有草稿均已保留' } : current)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '暂停任务失败')
    } finally {
      setCreationRunAction(null)
    }
  }

  const resumeCreationRun = async () => {
    if (!activeCreationRun?.operation_id || creationRunAction) return
    setCreationRunAction('resume')
    try {
      await apiClient.post(`/operations/${activeCreationRun.operation_id}/continue`)
      setActiveCreationRun((current) => current ? { ...current, status: 'running', current_message: '正在从最近检查点继续' } : current)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '继续任务失败')
    } finally {
      setCreationRunAction(null)
    }
  }

  const stopGeneration = async () => {
    projectStopRequestedRef.current = true
    const operationId = activeProjectOperationRef.current
    if (operationId) {
      try {
        await apiClient.post(`/operations/${operationId}/cancel`)
      } catch (error) {
        message.error(error instanceof Error ? error.message : '停止后台任务失败')
        projectStopRequestedRef.current = false
        return
      }
    } else {
      for (let attempt = 0; attempt < 20 && !activeProjectOperationRef.current; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 100))
      }
      if (activeProjectOperationRef.current) {
        try {
          await apiClient.post(`/operations/${activeProjectOperationRef.current}/cancel`)
        } catch (error) {
          message.error(error instanceof Error ? error.message : '停止后台任务失败')
          projectStopRequestedRef.current = false
          return
        }
      } else {
        message.warning('任务标识尚未返回，后台仍在建立任务，请稍后再次停止')
        projectStopRequestedRef.current = false
        return
      }
    }
    abortRef.current?.abort()
    setStreaming(false)
    setRunningStartTime(null)
    setMessages((prev) => {
      const next = [...prev]
      const last = next[next.length - 1]
      if (last?.role === 'assistant' && last?.status === 'running') {
        last.status = 'aborted'
        if (!last.content || last.content === '正在处理...' || last.content === '思考中...' || last.content === '正在分析需求...') {
          last.content = '已停止生成。'
        } else {
          last.content += '\n\n（已停止生成）'
        }
      }
      return [...next]
    })
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  // ── Creative Slots Editor ──
  const openSlotEditor = (index: number) => {
    const bp = systemBlueprints[index]
    if (!bp) return
    setSlotBlueprintIndex(index)
    setSlotDraft({ ...(bp.creative_slots || {}) } as Record<string, string | string[]>)
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
    await handleSystemAssistantMessage(feedback)
  }

  const saveBlueprintAsTemplate = (blueprint: NovelBlueprint) => {
    const nextTemplate: CreationTemplate = {
      id: Date.now().toString(),
      name: blueprint.title || '未命名模板',
      brief: [
        blueprint.premise || '',
        blueprint.core_conflict ? `核心冲突：${blueprint.core_conflict}` : '',
        blueprint.protagonist?.name ? `主角：${blueprint.protagonist.name}` : '',
      ].filter(Boolean).join('\n'),
      creative_slots: blueprint.creative_slots as Record<string, string | string[]> | undefined,
    }
    const next = [nextTemplate, ...creationTemplates].slice(0, 12)
    localStorage.setItem(CREATION_TEMPLATE_KEY, JSON.stringify(next))
    setCreationTemplates(next)
    message.success('已保存为新书创作模板')
  }

  const renderSlotEditorModal = () => {
    if (!slotEditorOpen) return null
    const SLOT_LABELS: [string, string][] = [
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
    ]
    return (
      <div style={{
        position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
        background: 'rgba(0,0,0,0.5)', zIndex: 1000,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <div style={{
          background: 'var(--ant-color-bg-elevated)', borderRadius: 8, padding: 24, width: 600,
          maxHeight: '80vh', overflow: 'auto',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
            <Text strong>
              编辑创意槽{slotBlueprintIndex !== null ? ` · 方案 ${slotBlueprintIndex + 1}` : ''}
            </Text>
            <Button size="small" onClick={closeSlotEditor}>✕</Button>
          </div>
          <div style={{ marginBottom: 16 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              这里改的是创作方向，不会直接覆盖原方案。确认后，助手会把这些槽位作为反馈重新调整方案。
            </Text>
          </div>
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {SLOT_LABELS.map(([key, label]) => (
              <div key={key}>
                <Text strong style={{ fontSize: 13 }}>{label}</Text>
                <Input.TextArea
                  value={slotValueToText(slotDraft[key])}
                  onChange={(e) => setSlotDraft((prev) => ({ ...prev, [key]: e.target.value }))}
                  autoSize={{ minRows: 2, maxRows: 5 }}
                  style={{ marginTop: 4 }}
                />
              </div>
            ))}
          </Space>
          <div style={{ display: 'flex', gap: 8, marginTop: 16, justifyContent: 'flex-end' }}>
            <Button onClick={closeSlotEditor}>取消</Button>
            <Button type="primary" onClick={submitSlotEditor}>按创意槽调整</Button>
          </div>
        </div>
      </div>
    )
  }

  const renderBlueprintCards = () => {
    if (!systemBlueprints.length) return null
    return (
      <div className="gui-chat-blueprints">
        <div className="gui-chat-blueprints-head">
          <div>
            <Text strong>新书方案</Text>
            <Text type="secondary">  ·  可继续对话微调，也可以直接创建</Text>
          </div>
          <Space size={8}>
            <Button size="small" onClick={() => handleSystemAssistantMessage('全部重新生成')} disabled={streaming}>
              重新生成
            </Button>
            <Button size="small" onClick={() => handleSystemAssistantMessage('强化书名、主角动机和前三章钩子')} disabled={streaming}>
              强化方案
            </Button>
          </Space>
        </div>
        <div className={`gui-chat-blueprint-grid${systemBlueprints.length === 1 ? ' gui-chat-blueprint-grid-single' : ''}`}>
          {systemBlueprints.map((bp, index) => {
            const score = bp.requirement_coverage?.score
            return (
              <Card
                key={`${bp.title}-${index}`}
                size="small"
                className="gui-chat-blueprint-card"
                title={<span className="gui-chat-blueprint-title">《{bp.title}》</span>}
                extra={score != null ? <Tag color={score >= 90 ? 'green' : 'blue'}>覆盖率 {score}%</Tag> : null}
              >
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Space size={6} wrap>
                    {bp.subtitle && <Tag color="purple">{bp.subtitle}</Tag>}
                    {bp.genre && <Tag>{bp.genre}</Tag>}
                    {bp.estimated_chapters && <Tag>{bp.estimated_chapters}章</Tag>}
                    <Tag>{(bp.characters?.length || 0) + 1} 角色</Tag>
                    <Tag>{bp.relationships?.length || 0} 关系</Tag>
                    <Tag>{bp.worldbuilding?.length || 0} 设定</Tag>
                    <Tag>{bp.volume_outline?.length || 0} 卷</Tag>
                    <Tag>{bp.outline?.length || 0} 节点</Tag>
                  </Space>
                  <Text type="secondary">主角：{bp.protagonist?.name || '待定主角'}</Text>
                  {bp.logline && <Paragraph className="gui-chat-blueprint-logline">{bp.logline}</Paragraph>}
                  {bp.premise && <Paragraph className="gui-chat-blueprint-premise">{bp.premise}</Paragraph>}
                  <Collapse
                    ghost
                    size="small"
                    items={[
                      {
                        key: 'details',
                        label: '查看完整方案',
                        children: (
                          <Space direction="vertical" size={10} style={{ width: '100%' }}>
                            {bp.selling_points?.length ? (
                              <div>
                                <Text strong>核心卖点</Text>
                                <ul className="gui-chat-blueprint-list">
                                  {bp.selling_points.slice(0, 4).map((point, pointIndex) => (
                                    <li key={`${bp.title}-selling-${pointIndex}`}>{point}</li>
                                  ))}
                                </ul>
                              </div>
                            ) : null}
                            {bp.world_hook && <Text>世界钩子：{bp.world_hook}</Text>}
                            {bp.core_conflict && <Text>核心冲突：{bp.core_conflict}</Text>}
                            {bp.protagonist?.goal && <Text>主角目标：{bp.protagonist.goal}</Text>}
                            {bp.protagonist?.weakness && <Text>主角弱点：{bp.protagonist.weakness}</Text>}
                            {bp.protagonist?.opening_pressure && <Text>开局压力：{bp.protagonist.opening_pressure}</Text>}
                            {bp.protagonist?.background && <Text type="secondary">主角背景：{bp.protagonist.background}</Text>}
                            {bp.golden_three && (
                              <div>
                                <Text strong>黄金三章</Text>
                                <Space direction="vertical" size={4} className="gui-chat-blueprint-details">
                                  {bp.golden_three.chapter_1 && <Text type="secondary">1：{bp.golden_three.chapter_1}</Text>}
                                  {bp.golden_three.chapter_2 && <Text type="secondary">2：{bp.golden_three.chapter_2}</Text>}
                                  {bp.golden_three.chapter_3 && <Text type="secondary">3：{bp.golden_three.chapter_3}</Text>}
                                </Space>
                              </div>
                            )}
                            {bp.volume_outline?.length ? (
                              <div>
                                <Text strong>卷纲概览</Text>
                                <Space direction="vertical" size={4} className="gui-chat-blueprint-details">
                                  {bp.volume_outline.slice(0, 4).map((volume, volumeIndex) => (
                                    <Text type="secondary" key={`${bp.title}-volume-${volumeIndex}`}>
                                      {volume.title || `第${volumeIndex + 1}卷`}
                                      {volume.summary ? `：${volume.summary}` : ''}
                                    </Text>
                                  ))}
                                </Space>
                              </div>
                            ) : null}
                            {bp.requirement_coverage?.missing?.length ? (
                              <Alert
                                type="warning"
                                showIcon
                                message="仍需补充"
                                description={bp.requirement_coverage.missing.slice(0, 3).join('；')}
                              />
                            ) : null}
                            {bp.quality_self_check?.issues?.length ? (
                              <Alert
                                type={bp.quality_self_check.pass ? 'info' : 'warning'}
                                showIcon
                                message={`质量自检 ${bp.quality_self_check.score ?? 0} 分`}
                                description={bp.quality_self_check.issues.slice(0, 2).join('；')}
                              />
                            ) : null}
                          </Space>
                        ),
                      },
                    ]}
                  />
                  <Button
                    type="primary"
                    block
                    loading={streaming}
                    disabled={streaming}
                    onClick={() => handleSystemAssistantMessage(`使用第${index + 1}个创建`)}
                  >
                    使用这个创建作品
                  </Button>
                  <Space size={4}>
                    <Button
                      size="small"
                      onClick={() => openSlotEditor(index)}
                      disabled={streaming}
                    >
                      编辑创意槽
                    </Button>
                    <Button
                      size="small"
                      onClick={() => saveBlueprintAsTemplate(bp)}
                    >
                      保存为模板
                    </Button>
                  </Space>
                </Space>
              </Card>
            )
          })}
        </div>
      </div>
    )
  }

  const renderCreationRunCard = () => {
    if (!activeCreationRun) return null
    const runArtifact = creationArtifacts.find((item) => item.artifact === activeCreationRun.stage)
    const artifactStatus = runArtifact?.stored_status || runArtifact?.status
    const artifactAlreadyConfirmed = ['waiting_user', 'waiting_author'].includes(activeCreationRun.status)
      && artifactStatus === 'confirmed'
      && !runArtifact?.conflict
    const modelPresentation = activeCreationRun.card_presentation
    const effectiveRunStatus = artifactAlreadyConfirmed
      ? 'completed'
      : modelPresentation?.status || activeCreationRun.status
    const effectiveRunMessage = artifactAlreadyConfirmed
      ? '阶段内容已由作者确认，立项数据和任务状态已同步。'
      : modelPresentation?.message || activeCreationRun.current_message
    const isRunning = ['queued', 'running'].includes(effectiveRunStatus)
    const isWaiting = ['waiting_user', 'waiting_author'].includes(effectiveRunStatus)
    const isPaused = effectiveRunStatus === 'paused'
    const canRetry = modelPresentation
      ? modelPresentation.show_retry
      : ['failed', 'cancelled', 'interrupted'].includes(effectiveRunStatus)
    const statusLabels: Record<string, string> = {
      queued: '排队中',
      running: '正在生成',
      waiting_user: '等待确认',
      waiting_author: '等待确认',
      paused: '已暂停',
      completed: '已完成',
      partial_success: '部分完成',
      failed: '失败',
      cancelled: '已取消',
      interrupted: '已中断',
    }
    const stageLabels: Record<string, string> = {
      concepts: '创意方向',
      world_style: '文风与世界观',
      characters: '角色与关系',
      locations: '地点与势力',
      macro_outline: '主线与卷纲',
      opening_outline: '开篇细纲',
      final_review: '完整性检查',
      all: '完整立项',
    }
    return (
      <Card className={`gui-chat-creation-run gui-chat-creation-run-${effectiveRunStatus}`} variant="outlined">
        <div className="gui-chat-creation-run-head">
          <div>
            <Text type="secondary" className="gui-chat-creation-run-kicker">立项任务</Text>
            <Title level={5}>{stageLabels[activeCreationRun.stage] || activeCreationRun.stage}</Title>
          </div>
          <Tag color={isRunning ? 'processing' : isWaiting ? 'gold' : effectiveRunStatus === 'failed' ? 'error' : ['completed', 'partial_success'].includes(effectiveRunStatus) ? 'success' : 'default'}>
            {modelPresentation?.label || statusLabels[effectiveRunStatus] || effectiveRunStatus}
          </Tag>
        </div>
        <Paragraph className="gui-chat-creation-run-message">
          {effectiveRunMessage || '任务状态正在同步…'}
        </Paragraph>
        <Space size={[8, 8]} wrap>
          {activeCreationRun.model_source && <Tag>模型：{activeCreationRun.model_source}</Tag>}
          {activeCreationRun.attempt != null && <Tag>尝试：{activeCreationRun.attempt}</Tag>}
          {activeCreationRun.result_mode && <Tag>结果：{activeCreationRun.result_mode}</Tag>}
        </Space>
        <div className="gui-chat-creation-run-actions">
          {isRunning && (
            <>
              <Button icon={<PauseCircleOutlined />} loading={creationRunAction === 'pause'} onClick={() => void pauseCreationRun()}>
                暂停
              </Button>
              <Button danger icon={<StopOutlined />} loading={creationRunAction === 'cancel'} onClick={() => void cancelCreationRun()}>
                停止
              </Button>
            </>
          )}
          {isPaused && <Button type="primary" icon={<PlayCircleOutlined />} loading={creationRunAction === 'resume'} onClick={() => void resumeCreationRun()}>继续</Button>}
          {canRetry && (
            <>
              <Button icon={<ReloadOutlined />} loading={creationRunAction === 'retry-original'} onClick={() => void retryCreationRun(false)}>
                按原输入重试
              </Button>
              <Button type="primary" icon={<ReloadOutlined />} loading={creationRunAction === 'retry-latest'} onClick={() => void retryCreationRun(true)}>
                按最新内容重试
              </Button>
            </>
          )}
          <Button
            icon={<FolderOpenOutlined />}
            onClick={() => {
              setCreationPanelOpen(true)
              const artifact = creationArtifacts.find((item) => item.artifact === activeCreationRun.stage) || {
                artifact: activeCreationRun.stage,
                label: activeCreationRun.stage,
                status: 'pending' as const,
                revision: 0,
              }
              void openArtifactEditor(artifact, activeCreationRun.session_id)
            }}
          >
            打开完整编辑器
          </Button>
        </div>
      </Card>
    )
  }

  const renderMaterialImportCard = () => {
    if (!activeMaterialImport) return null
    const running = ['queued', 'running'].includes(activeMaterialImport.status)
    const retryable = ['failed', 'cancelled', 'interrupted'].includes(activeMaterialImport.status)
    const preview = activeMaterialImport.preview
    const detected = preview?.detected || {}
    const progress = activeMaterialImport.chunk_count > 0
      ? Math.round((activeMaterialImport.processed_chunks / activeMaterialImport.chunk_count) * 100)
      : running ? 4 : 0
    const statusLabels: Record<string, string> = {
      queued: '等待处理', running: '正在整理', waiting_user: '等待预览确认',
      completed: '已导入', failed: '导入失败', cancelled: '已取消', interrupted: '已中断',
    }
    return (
      <Card className={`gui-chat-creation-run gui-chat-creation-run-${activeMaterialImport.status}`} variant="outlined">
        <div className="gui-chat-creation-run-head">
          <div>
            <Text type="secondary" className="gui-chat-creation-run-kicker">资料导入</Text>
            <Title level={5}>{activeMaterialImport.filename}</Title>
          </div>
          <Tag color={running ? 'processing' : activeMaterialImport.status === 'waiting_user' ? 'gold' : activeMaterialImport.status === 'failed' ? 'error' : 'default'}>
            {statusLabels[activeMaterialImport.status] || activeMaterialImport.status}
          </Tag>
        </div>
        {running && (
          <Progress
            percent={progress}
            size="small"
            status="active"
            format={() => activeMaterialImport.chunk_count > 0
              ? `${activeMaterialImport.processed_chunks}/${activeMaterialImport.chunk_count} 块`
              : '读取中'}
          />
        )}
        {preview && (
          <Space size={[6, 6]} wrap>
            <Tag>人物 {detected.characters || 0}</Tag>
            <Tag>势力 {detected.factions || 0}</Tag>
            <Tag>地点 {detected.locations || 0}</Tag>
            <Tag>卷纲 {detected.volumes || 0}</Tag>
            <Tag>章节摘要 {detected.chapter_summaries || 0}</Tag>
            <Tag color={preview.conflicts?.length ? 'orange' : 'green'}>冲突 {preview.conflicts?.length || 0}</Tag>
          </Space>
        )}
        {activeMaterialImport.error && (
          <Alert
            type="error"
            showIcon
            message="发生了什么"
            description={`${activeMaterialImport.error}；原始文件和已完成分块仍已保存，立项数据没有写入。`}
          />
        )}
        <div className="gui-chat-creation-run-actions">
          {activeMaterialImport.status === 'waiting_user' && (
            <Button type="primary" icon={<DatabaseOutlined />} onClick={() => setImportPreviewOpen(true)}>
              预览并选择导入
            </Button>
          )}
          {retryable && (
            <Button icon={<ReloadOutlined />} loading={importActionLoading} onClick={() => void retryMaterialImport()}>
              从检查点重试
            </Button>
          )}
          {running && activeMaterialImport.operation_id && (
            <Button
              danger
              icon={<StopOutlined />}
              onClick={() => void apiClient.post(`/operations/${activeMaterialImport.operation_id}/cancel`)}
            >
              停止
            </Button>
          )}
        </div>
      </Card>
    )
  }

  const confirmArtifactFromPanel = async (artifact: CreationArtifactSummary) => {
    if (!systemSessionId || artifactAction) return
    setArtifactAction(artifact.artifact)
    try {
      const confirmed = await apiClient.post<ApiResponse<{ runs?: NovelCreationRunSummary[] }>>(`/novel-creation/sessions/${systemSessionId}/stages/${artifact.artifact}/confirm`, {
        confirm: true,
        source: 'author',
        expected_revision: artifact.revision,
      })
      const confirmedRun = [...(confirmed.data.data.runs || [])]
        .reverse()
        .find((run) => run.stage === artifact.artifact)
      setActiveCreationRun((current) => {
        if (!current || current.stage !== artifact.artifact) return current
        if (confirmedRun && (confirmedRun.id || confirmedRun.run_id) === (current.id || current.run_id)) return confirmedRun
        return { ...current, status: 'completed', current_message: '阶段内容已由作者确认' }
      })
      if (artifact.artifact === 'final_review') {
        const applied = await apiClient.post<ApiResponse<{ project_id: string; warnings?: string[] }>>(
          '/novel-creation/apply',
          { session_id: systemSessionId, mode: 'auto' },
        )
        const projectId = applied.data.data.project_id
        if (!projectId) throw new Error('最终审阅已确认，但正式作品没有创建成功。')
        message.success('最终审阅已确认，小说项目已创建。')
        await Promise.all([fetchProjects(), fetchCreationSessions()])
        navigate(`/project/${projectId}`)
      } else {
        message.success(`${artifact.label}已确认`)
      }
      await fetchCreationArtifacts()
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : '确认失败，立项数据未改变')
      await fetchCreationArtifacts()
    } finally {
      setArtifactAction(null)
    }
  }

  const undoArtifactFromPanel = async (artifact: CreationArtifactSummary) => {
    if (!systemSessionId || artifactAction || !artifact.can_undo) return
    setArtifactAction(`undo:${artifact.artifact}`)
    try {
      await apiClient.post(`/novel-creation/sessions/${systemSessionId}/artifacts/${artifact.artifact}/undo`, {
        expected_revision: artifact.revision,
      })
      message.success(`${artifact.label}已撤销最近一次修改`)
      await fetchCreationArtifacts()
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : '撤销失败，立项数据未改变')
      await fetchCreationArtifacts()
    } finally {
      setArtifactAction(null)
    }
  }

  const selectArtifactVersion = async (version: CreationArtifactVersionSummary) => {
    setVersionHistoryLoading(true)
    try {
      const response = await apiClient.get<ApiResponse<CreationArtifactVersionDetail>>(
        `/novel-creation/artifact-versions/${version.id}`,
      )
      setSelectedArtifactVersion(response.data.data)
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : '版本差异加载失败')
    } finally {
      setVersionHistoryLoading(false)
    }
  }

  const openArtifactVersionHistory = async (artifact: CreationArtifactSummary) => {
    if (!systemSessionId) return
    setVersionHistoryArtifact(artifact)
    setArtifactVersions([])
    setSelectedArtifactVersion(null)
    setVersionHistoryLoading(true)
    try {
      const response = await apiClient.get<ApiResponse<{ versions: CreationArtifactVersionSummary[] }>>(
        `/novel-creation/sessions/${systemSessionId}/artifacts/${artifact.artifact}/versions`,
      )
      const versions = response.data.data.versions || []
      setArtifactVersions(versions)
      if (versions[0]) await selectArtifactVersion(versions[0])
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : '版本历史加载失败')
      setVersionHistoryArtifact(null)
    } finally {
      setVersionHistoryLoading(false)
    }
  }

  const restoreSelectedArtifactVersion = async () => {
    if (!selectedArtifactVersion || !versionHistoryArtifact || artifactAction) return
    setArtifactAction(`restore:${selectedArtifactVersion.version.id}`)
    try {
      await apiClient.post(`/novel-creation/artifact-versions/${selectedArtifactVersion.version.id}/restore`, {
        expected_revision: versionHistoryArtifact.revision,
      })
      message.success(`${versionHistoryArtifact.label}已恢复到修订 ${selectedArtifactVersion.version.revision}；恢复前内容仍在历史中`)
      setVersionHistoryArtifact(null)
      setSelectedArtifactVersion(null)
      await fetchCreationArtifacts()
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : '版本恢复失败，当前立项数据没有变化')
      await fetchCreationArtifacts()
    } finally {
      setArtifactAction(null)
    }
  }

  const compactVersionValue = (value: unknown) => {
    if (value === undefined) return '—'
    const text = typeof value === 'string' ? value : JSON.stringify(value)
    return text.length > 120 ? `${text.slice(0, 117)}…` : text
  }

  const creationStatusLabel: Record<CreationArtifactSummary['status'], string> = {
    pending: '待生成',
    generated: '待确认',
    confirmed: '已确认',
    stale: '需校验',
    conflict: '版本冲突',
  }

  const creationStatusColor: Record<CreationArtifactSummary['status'], string> = {
    pending: 'default',
    generated: 'processing',
    confirmed: 'success',
    stale: 'warning',
    conflict: 'error',
  }

  const renderCreationDataPanel = () => {
    if (!systemSessionId) return null
    return (
      <aside className={`gui-chat-creation-panel${creationPanelOpen ? ' gui-chat-creation-panel-open' : ''}${expandedArtifact ? ' gui-chat-creation-panel-editor-open' : ''}`} aria-label="作品资料">
        <div className="gui-chat-creation-panel-head">
          <div>
            <Text className="gui-chat-creation-panel-kicker">STORY CONTEXT</Text>
            <Title level={5}>作品资料</Title>
          </div>
          <Space size={4}>
            {expandedArtifact && (
              <Tooltip title="退出完整编辑器，返回作品资料">
                <Button
                  size="small"
                  type="text"
                  icon={<ArrowLeftOutlined />}
                  onClick={() => void closeArtifactEditor()}
                >
                  返回作品资料
                </Button>
              </Tooltip>
            )}
            <Tooltip title="检查引用、失效数据和对象关系">
              <Button
                size="small"
                type="text"
                loading={creationConsistencyLoading}
                aria-label="检查作品资料一致性"
                onClick={() => void checkCreationConsistency(true)}
              >
                校验
              </Button>
            </Tooltip>
            <Tooltip title="刷新作品资料">
              <Button
                size="small"
                type="text"
                icon={<ReloadOutlined />}
                loading={creationArtifactsLoading}
                aria-label="刷新作品资料"
                onClick={() => void fetchCreationArtifacts()}
              />
            </Tooltip>
            <Tooltip title="收起作品资料">
              <Button
                size="small"
                type="text"
                icon={<MenuFoldOutlined />}
                aria-label="收起作品资料"
                onClick={() => {
                  setCreationPanelOpen(false)
                  void closeArtifactEditor()
                }}
              />
            </Tooltip>
          </Space>
        </div>
        <div className="gui-chat-creation-panel-summary">
          <Text type="secondary">
            这里统一维护立项与正文事实。创作约束、创意方向和文风可随时修改，并会进入后续项目助手与正文创作上下文。
          </Text>
          {creationConsistency && (
            <div className={`gui-chat-consistency-summary${creationConsistency.valid ? ' is-valid' : ' has-issues'}`}>
              <Tag color={creationConsistency.valid ? 'success' : creationConsistency.summary.blocking ? 'error' : undefined}>
                {creationConsistency.valid
                  ? '一致性通过'
                  : `${creationConsistency.summary.blocking} 个错误 · ${creationConsistency.summary.warnings} 个提醒`}
              </Tag>
              {!creationConsistency.valid && creationConsistency.issues[0] && (
                <Text type="secondary" ellipsis={{ tooltip: creationConsistency.issues[0].message }}>
                  {creationConsistency.issues[0].message}
                </Text>
              )}
            </div>
          )}
        </div>
        {expandedArtifact && (
          <div className="gui-chat-creation-inline-editor">
            <div className="gui-chat-creation-editor-intro">
              <div>
                <Text className="gui-chat-creation-editor-kicker">STRUCTURED MANUSCRIPT</Text>
                <Title level={3}>{expandedArtifact.label}</Title>
                <Paragraph>直接维护作品事实。停止输入 5 秒后自动保存，也可以在左侧聊天中让司命代为修改。</Paragraph>
              </div>
              <Space direction="vertical" align="end" size={8}>
                <Tag color={creationStatusColor[expandedArtifact.status]}>{creationStatusLabel[expandedArtifact.status]}</Tag>
                <span className={`gui-chat-creation-save-state${artifactEditorSaving ? ' is-saving' : artifactEditorDirty ? ' is-dirty' : ' is-saved'}`}>
                  <span className="gui-chat-creation-save-dot" />
                  {artifactEditorSaving ? '正在保存' : artifactEditorDirty ? '5 秒后自动保存' : artifactEditorSavedAt ? `已保存 ${artifactEditorSavedAt}` : '修改后自动保存'}
                </span>
              </Space>
            </div>
            {expandedArtifact.flow?.soft_dependencies?.length ? <Alert type="info" showIcon message={`可先编辑；补充 ${expandedArtifact.flow.soft_dependencies.map((item) => item.label).join('、')} 后质量会更稳定`} /> : null}
            {artifactEditorError ? <Alert type="error" showIcon message="自动保存失败" description={artifactEditorError} /> : null}
            <div className="gui-chat-creation-form">
              <StructuredStageEditor
                data={artifactEditorData}
                onChange={(data) => {
                  setArtifactEditorData(data)
                  setArtifactEditorDirty(true)
                  setArtifactEditorError(null)
                }}
              />
            </div>
          </div>
        )}
        <div className="gui-chat-creation-artifacts">
          {creationArtifactsLoading && creationArtifacts.length === 0 ? (
            <div className="gui-chat-creation-panel-loading"><Spin size="small" /> 正在同步…</div>
          ) : creationArtifacts.map((artifact) => (
            <section
              key={artifact.artifact}
              className={`gui-chat-creation-artifact gui-chat-creation-artifact-${artifact.status}`}
              title="点击“进入编辑器”，或双击卡片打开完整编辑器"
              onDoubleClick={() => void openArtifactEditor(artifact)}
            >
              <div className="gui-chat-creation-artifact-head">
                <Text strong ellipsis={{ tooltip: artifact.label }}>{artifact.label}</Text>
                <Tag color={creationStatusColor[artifact.status]}>{creationStatusLabel[artifact.status]}</Tag>
              </div>
              <div className="gui-chat-creation-artifact-meta">
                <span>来源：{artifact.source && artifact.source !== 'unknown' ? artifact.source : '尚未生成'}</span>
                <span>{artifact.locked_paths?.length ? `已锁定 ${artifact.locked_paths.length} 项` : '未锁定字段'}</span>
              </div>
              {artifact.running_operation && (
                <div className="gui-chat-creation-artifact-running">
                  <span className="siming-status-dot" /> {artifact.running_operation.current_message || '任务正在运行'}
                </div>
              )}
              {artifact.stale_reason && <Text type="warning" className="gui-chat-creation-artifact-warning">{artifact.stale_reason}</Text>}
              {artifact.conflict && (
                <Text type="danger" className="gui-chat-creation-artifact-warning">
                  旧任务结果未覆盖当前内容；候选稿已保留，可按原输入或最新内容重试
                </Text>
              )}
              {artifact.flow?.blocked_by?.length ? (
                <Text type="secondary" className="gui-chat-creation-artifact-blocked">
                  建议先完善：{artifact.flow.blocked_by.map((item) => item.label).join('、')}
                </Text>
              ) : null}
              {artifact.flow?.soft_dependencies?.length ? (
                <Text type="secondary" className="gui-chat-creation-artifact-blocked">
                  可先生成；补充{artifact.flow.soft_dependencies.map((item) => item.label).join('、')}后质量会更稳定
                </Text>
              ) : null}
              <div className="gui-chat-creation-artifact-actions">
                <Button
                  size="small"
                  icon={<DatabaseOutlined />}
                  loading={artifactDetailLoading}
                  onClick={() => void openArtifactEditor(artifact)}
                >
                  进入编辑器
                </Button>
                {artifact.flow?.can_confirm && (
                  <Button
                    size="small"
                    type="primary"
                    aria-label={`确认${artifact.label}`}
                    loading={artifactAction === artifact.artifact}
                    onClick={() => void confirmArtifactFromPanel(artifact)}
                  >
                    确认
                  </Button>
                )}
                {artifact.can_undo && (
                  <Button
                    size="small"
                    icon={<UndoOutlined />}
                    aria-label={`撤销${artifact.label}最近一次修改`}
                    loading={artifactAction === `undo:${artifact.artifact}`}
                    onClick={() => void undoArtifactFromPanel(artifact)}
                  >
                    撤销
                  </Button>
                )}
                {artifact.status !== 'pending' && (
                  <Button
                    size="small"
                    icon={<HistoryOutlined />}
                    aria-label={`查看${artifact.label}版本历史`}
                    onClick={() => void openArtifactVersionHistory(artifact)}
                  >
                    历史{artifact.version_count ? ` ${artifact.version_count}` : ''}
                  </Button>
                )}
                <Button
                  size="small"
                  onClick={() => {
                    setInputValue(`${artifact.status === 'pending' ? '生成' : '调整'}${artifact.label}：`)
                    setCreationPanelOpen(false)
                  }}
                >
                  {artifact.status === 'pending' ? '在聊天中生成' : '继续调整'}
                </Button>
              </div>
            </section>
          ))}
        </div>
      </aside>
    )
  }

  const runtimeHasProblem = creationAgentRuntime.quota_status === 'exhausted_or_limited'
  const runtimePanel = (
    <div className="gui-chat-runtime-panel" aria-label="当前模型运行状态">
      <div className="gui-chat-runtime-panel-head">
        <Text strong>本次对话模型</Text>
        <Tag color={runtimeHasProblem ? 'error' : 'success'}>{runtimeHasProblem ? '需要处理' : '可用'}</Tag>
      </div>
      <Select
        className="gui-chat-model-select"
        showSearch
        allowClear
        value={selectedModel}
        onChange={(value) => setSelectedModelOverride(value || undefined)}
        options={modelOptions}
        loading={modelsLoading}
        optionFilterProp="label"
        placeholder="选择本次对话模型"
        aria-label="选择本次对话模型"
        title={selectedModel || '未配置模型'}
      />
      <dl className="gui-chat-runtime-list">
        <div><dt>提供商</dt><dd>{creationAgentRuntime.provider || '未配置'}</dd></div>
        <div><dt>模型</dt><dd>{creationAgentRuntime.effective_model || '未配置'}</dd></div>
        <div><dt>来源</dt><dd>{runtimeSourceLabel[creationAgentRuntime.model_source || 'unknown'] || '待确认'}</dd></div>
        <div><dt>工具模式</dt><dd>{runtimeToolModeLabel.replace('工具模式：', '')}</dd></div>
        <div>
          <dt>运行时限</dt>
          <dd>{creationAgentRuntime.timeout_seconds ? `${creationAgentRuntime.timeout_seconds} 秒` : '不设总时限，按活动检测'}</dd>
        </div>
        <div><dt>额度</dt><dd>{runtimeQuotaLabel.replace('额度：', '')}</dd></div>
      </dl>
      {runtimeHasProblem && <Text type="danger">请切换有额度的模型后重试当前操作。</Text>}
    </div>
  )

  return (
    <div className={`gui-chat${sidebarCollapsed ? ' gui-chat-collapsed' : ''}${systemSessionId && creationPanelOpen ? ' gui-chat-with-creation-panel' : ''}${systemSessionId && creationPanelOpen && expandedArtifact ? ' gui-chat-with-creation-editor' : ''}`}>
      <aside className="gui-chat-sidebar">
        <div className="gui-chat-sidebar-head">
          {!sidebarCollapsed && (
            <Title level={5} style={{ margin: 0 }}>
              <RobotOutlined /> 对话列表
            </Title>
          )}
          <Space>
            {!sidebarCollapsed && (
              <>
                <Tooltip title="刷新对话">
                  <Button
                    icon={<ReloadOutlined />}
                    size="small"
                    aria-label="刷新对话"
                    onClick={() => fetchConversations()}
                    loading={conversationsLoading}
                  />
                </Tooltip>
                <Tooltip title="新对话">
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    size="small"
                    aria-label="新对话"
                    onClick={startNewConversation}
                  />
                </Tooltip>
              </>
            )}
            <Tooltip title={sidebarCollapsed ? '展开对话列表' : '收起对话列表'}>
              <Button
                icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                size="small"
                aria-label={sidebarCollapsed ? '展开对话列表' : '收起对话列表'}
                onClick={() => setSidebarCollapsed((value) => !value)}
              />
            </Tooltip>
          </Space>
        </div>
        {!sidebarCollapsed && (
          <div className="gui-chat-conv-list">
            {conversations.length === 0 && !conversationsLoading ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无对话" style={{ padding: '40px 0' }} />
            ) : (
              conversations.map((conv) => (
                <div
                  key={conv.id}
                  className={`gui-chat-conv-item${conv.id === activeConvId ? ' gui-chat-conv-item-active' : ''}`}
                  onClick={() => fetchMessages(conv.id)}
                >
                  <span className="gui-chat-conv-title">{conv.title || '未命名对话'}</span>
                  <Button
                    type="text"
                    size="small"
                    icon={<DeleteOutlined />}
                    aria-label="删除对话"
                    danger
                    onClick={(e) => {
                      e.stopPropagation()
                      deleteConversation(conv.id)
                    }}
                  />
                </div>
              ))
            )}
          </div>
        )}
      </aside>

      <main className="gui-chat-main">
        <div className="gui-chat-header">
          <div className="gui-chat-heading">
            <Title level={5} style={{ margin: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {activeConvId ? conversations.find((c) => c.id === activeConvId)?.title || 'AI 助手' : 'AI 助手'}
            </Title>
            <Text type="secondary" className="gui-chat-project-line">
              <FolderOpenOutlined /> {assistantContextLabel}
            </Text>
          </div>
          <Space className="gui-chat-header-actions">
            <span id="global-operation-nav-slot" className="global-operation-nav-slot" />
            {systemSessionId && !creationPanelOpen && (
              <Button icon={<DatabaseOutlined />} onClick={() => setCreationPanelOpen(true)}>
                作品资料
              </Button>
            )}
            <Button
              icon={<HistoryOutlined />}
              onClick={() => setSidebarCollapsed((value) => !value)}
              aria-expanded={!sidebarCollapsed}
            >
              对话记录
            </Button>
            <Select
              showSearch
              allowClear
              value={activeProjectId ? `project:${activeProjectId}` : systemSessionId ? `creation:${systemSessionId}` : undefined}
              onChange={(value) => void selectAssistantContext(value)}
              onOpenChange={(open) => {
                if (open) void fetchCreationSessions()
              }}
              options={assistantContextOptions}
              loading={projectsLoading}
              optionFilterProp="label"
              placeholder="选择作品上下文"
              className="gui-chat-project-select"
              aria-label="选择作品上下文"
            />
            <Popover content={runtimePanel} trigger="click" placement="bottomRight">
              <Button
                icon={<InfoCircleOutlined />}
                aria-label="查看当前模型与运行状态"
                danger={runtimeHasProblem}
              >
                <span className={`siming-status-dot${runtimeHasProblem ? ' siming-status-dot-error' : ''}`} />
                {creationAgentRuntime.provider || '配置模型'}
              </Button>
            </Popover>
            <Tooltip title="新对话">
              <Button type="primary" icon={<PlusOutlined />} aria-label="新对话" onClick={startNewConversation} />
            </Tooltip>
          </Space>
        </div>

        <div className="gui-chat-messages" aria-live="polite" aria-busy={streaming || loading}>
          {!selectedModel && (
            <Alert
              className="gui-chat-setup-alert"
              type="warning"
              showIcon
              icon={<RocketOutlined />}
              message="还差一步：先连接一个模型"
              description="可以免费开始，不需要 API Key，也不用打开命令行。"
              action={<Button type="primary" onClick={() => navigate('/getting-started')}>免费设置</Button>}
            />
          )}
          {!activeProjectId && !projectsLoading && messages.length === 0 ? (
            <div className="gui-chat-welcome">
              <div className="gui-chat-welcome-icon" aria-hidden="true">
                <RobotOutlined />
              </div>
              <Title level={3} style={{ margin: '0 0 8px', fontFamily: "'Noto Serif SC', serif" }}>
                司命系统助手
              </Title>
              <Paragraph type="secondary" style={{ fontSize: 15, maxWidth: 460, textAlign: 'center' }}>
                不需要先创建作品。你可以直接说"我想写1000章，克苏鲁+修仙+规则怪谈"，我会生成新书方案，并在你确认后创建作品。
              </Paragraph>
              <Space wrap className="gui-chat-welcome-actions">
                <Button type="primary" icon={<PlusOutlined />} size="large" onClick={() => navigate('/novel-creation')}>
                  开始新书立项
                </Button>
                <Button size="large" onClick={() => setInputValue('我想写一本新的小说，先和我聊聊想法')}>
                  先聊聊想法
                </Button>
              </Space>
            </div>
          ) : activeProjectId && !activeConvId && messages.length === 0 ? (
            <div className="gui-chat-welcome">
              <div className="gui-chat-welcome-icon" aria-hidden="true">
                <RobotOutlined />
              </div>
              <Title level={3} style={{ margin: '0 0 8px', fontFamily: "'Noto Serif SC', serif" }}>
                司命 AI 助手
              </Title>
              <Paragraph type="secondary" style={{ fontSize: 15, maxWidth: 460, textAlign: 'center' }}>
                当前绑定作品：{activeProject?.title || '未选择'}。写章节、查角色会进入作品助手；创建新小说会自动切到系统立项流程。
              </Paragraph>
              <Space wrap className="gui-chat-welcome-actions">
                <Button type="primary" icon={<PlusOutlined />} size="large" onClick={startNewConversation}>
                  开始新对话
                </Button>
                <Button size="large" onClick={() => setInputValue('帮我创建一本新的小说，克苏鲁+规则怪谈，至少要能写1000章的创意')}>
                  创建新小说
                </Button>
              </Space>
            </div>
          ) : loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
              <Spin size="large" />
            </div>
          ) : (
            <>
              {messages.map((msg, index) => (
                <div
                  key={msg.id || `${msg.role}-${index}`}
                  className={`gui-chat-msg gui-chat-msg-${msg.role}`}
                  data-message-status={msg.status || 'completed'}
                  role={msg.status === 'error' ? 'alert' : undefined}
                  aria-live={msg.status === 'error' ? 'assertive' : undefined}
                >
                  <div className="gui-chat-msg-role">
                    <span>{msg.role === 'user' ? '你' : '司命'}</span>
                    <AssistantMessageTime value={msg.created_at} />
                  </div>
                  <div className="gui-chat-msg-content">
                    {msg.status === 'error' && <Tag color="error" className="gui-chat-msg-status">执行失败</Tag>}
                    {msg.status === 'aborted' && <Tag color="default" className="gui-chat-msg-status">已停止</Tag>}
                    {msg.content || (streaming && msg.role === 'assistant' ? '思考中...' : '')}
                    {msg.status === 'running' && elapsedSeconds > 0 && (
                      <span style={{ color: 'var(--ant-color-text-tertiary)', fontSize: 12, marginLeft: 8 }}>
                        ⏱ {elapsedSeconds}s
                      </span>
                    )}
                    {msg.status === 'error' && (
                      <div className="gui-chat-error-actions">
                        <Button
                          size="small"
                          onClick={() => {
                            const previousPrompt = messages.slice(0, index).reverse().find((item) => item.role === 'user')?.content
                            if (previousPrompt) setInputValue(previousPrompt)
                          }}
                        >
                          放回输入框重试
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {renderBlueprintCards()}
              {renderMaterialImportCard()}
              {renderCreationRunCard()}
              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        <div className="gui-chat-composer">
          {renderPendingFiles()}
          {pendingInputClarification && (
            <Alert
              type="info"
              showIcon
              message={`正在确认「${pendingInputClarification.source.name}」的处理方式`}
              description="原始内容和此前回答已保留；请回答上一个问题。若仍有歧义，司命会每轮只追问一个关键点。"
              action={(
                <Button size="small" onClick={() => setPendingInputClarification(null)}>
                  取消处理
                </Button>
              )}
            />
          )}
          {isLocalCliModel && systemSessionId && (
            <Alert
              className="gui-chat-cli-permission"
              type={nextLocalCliGrant ? 'warning' : 'info'}
              showIcon
              message={nextLocalCliGrant ? '下一条消息可修改当前立项数据' : '本机 CLI 已安全内化到聊天'}
              description={nextLocalCliGrant
                ? supportsTransientCreationMcp
                  ? '仅下一条有效；OpenCode 将直连当前会话的临时 Siming MCP，结束即销毁，不改全局配置。'
                  : '仅下一条有效；司命通过受控工具桥校验并执行立项操作，CLI 无法访问项目目录或全局配置。'
                : '默认可对话和读取立项数据；需要写入时由你单次授权。消息中出现本地路径时会另行确认，并只提供临时只读快照。不会自动扫描或修改全局 MCP 配置。'}
              action={nextLocalCliGrant ? (
                <Button size="small" onClick={() => setNextLocalCliGrant(false)}>取消授权</Button>
              ) : (
                <Button
                  size="small"
                  onClick={() => {
                    Modal.confirm({
                      title: '仅授权下一条消息修改立项？',
                      okText: '仅授权下一条',
                      cancelText: '保持只读',
                      content: (
                        <div className="gui-chat-cli-grant-copy">
                          <p>{supportsTransientCreationMcp
                            ? 'OpenCode 会直接连接只包含当前立项会话工具的临时 Siming MCP。'
                            : '司命会通过受控工具桥执行经过白名单校验的立项工具。'}</p>
                          <p>CLI 不会获得项目文件、Shell 或其他程序的全局配置权限。</p>
                          <p>授权在下一条消息结束后自动失效。</p>
                        </div>
                      ),
                      onOk: () => setNextLocalCliGrant(true),
                    })
                  }}
                >
                  授权下一条修改
                </Button>
              )}
            />
          )}
          <Input.TextArea
            aria-label="给司命的消息"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={pendingInputClarification
              ? '回答上一个问题…'
              : pendingFiles.length > 0
                ? '可补充处理要求；留空时会读取文件内的意图…'
                : '告诉司命你想创作或处理什么...'}
            autoSize={{ minRows: 2, maxRows: 6 }}
            maxLength={CHAT_MESSAGE_CHAR_LIMIT}
            showCount={{ formatter: ({ count }) => `${count.toLocaleString('zh-CN')} / 100 万字` }}
            disabled={streaming}
          />
          <div className="gui-chat-composer-actions">
            <Text type="secondary" className="gui-chat-composer-hint">Enter 发送 · Shift + Enter 换行</Text>
            {streaming ? (
              <Button icon={<StopOutlined />} onClick={stopGeneration} danger>
                停止生成
              </Button>
            ) : (
              <>
                <Upload
                  accept=".txt,.md,.docx,.json"
                  maxCount={1}
                  showUploadList={false}
                  beforeUpload={(file) => {
                    handleFileImport(file as File)
                    return false
                  }}
                >
                  <Button icon={<FileAddOutlined />} title="导入文件作为参考" />
                </Upload>
                <Button
                  type="primary"
                  icon={<SendOutlined />}
                  onClick={() => void sendMessage()}
                  disabled={!inputValue.trim() && pendingFiles.length === 0}
                >
                  发送
                </Button>
              </>
            )}
          </div>
        </div>
      </main>
      {renderCreationDataPanel()}
      {renderSlotEditorModal()}
      <Modal
        title={`版本历史 · ${versionHistoryArtifact?.label || ''}`}
        open={Boolean(versionHistoryArtifact)}
        onCancel={() => {
          setVersionHistoryArtifact(null)
          setSelectedArtifactVersion(null)
        }}
        width={880}
        styles={{ body: { maxHeight: 'min(660px, calc(100vh - 250px))', overflowY: 'auto', paddingRight: 6 } }}
        footer={[
          <Button key="close" onClick={() => setVersionHistoryArtifact(null)}>关闭</Button>,
          <Button
            key="restore"
            type="primary"
            icon={<HistoryOutlined />}
            loading={Boolean(selectedArtifactVersion && artifactAction === `restore:${selectedArtifactVersion.version.id}`)}
            disabled={!selectedArtifactVersion || selectedArtifactVersion.version.id === artifactVersions[0]?.id}
            onClick={() => void restoreSelectedArtifactVersion()}
          >
            {selectedArtifactVersion?.version.id === artifactVersions[0]?.id ? '当前版本' : '恢复此版本'}
          </Button>,
        ]}
      >
        <Alert
          type="info"
          showIcon
          message="每次保存、AI 调整、导入、锁定和恢复都会留下不可变版本"
          description="恢复不会删除较新的内容；恢复前状态仍会保留在历史中，可再次查看或恢复。"
        />
        <div className="gui-chat-version-history" aria-label={`${versionHistoryArtifact?.label || ''}版本列表与差异`}>
          <div className="gui-chat-version-list" role="list" aria-label="版本列表">
            {artifactVersions.length === 0 && !versionHistoryLoading ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无版本记录" />
            ) : artifactVersions.map((version, index) => (
              <button
                type="button"
                role="listitem"
                key={version.id}
                className={`gui-chat-version-item${selectedArtifactVersion?.version.id === version.id ? ' gui-chat-version-item-active' : ''}`}
                onClick={() => void selectArtifactVersion(version)}
              >
                <span className="gui-chat-version-item-head">
                  <strong>修订 {version.revision}</strong>
                  {index === 0 && <Tag color="success">当前</Tag>}
                </span>
                <span>{version.source || 'unknown'} · {version.change_type}</span>
                <span>{version.created_at ? new Date(version.created_at).toLocaleString('zh-CN') : '时间未记录'}</span>
              </button>
            ))}
          </div>
          <div className="gui-chat-version-diff" aria-live="polite">
            {versionHistoryLoading && !selectedArtifactVersion ? (
              <div className="gui-chat-creation-panel-loading"><Spin size="small" /> 正在读取差异…</div>
            ) : selectedArtifactVersion ? (
              <>
                <div className="gui-chat-version-diff-head">
                  <Text strong>与{selectedArtifactVersion.against ? `修订 ${selectedArtifactVersion.against.revision}` : '空白版本'}相比</Text>
                  <Tag>{selectedArtifactVersion.change_count} 处变化</Tag>
                </div>
                {selectedArtifactVersion.changes.length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="内容没有变化（可能只更新了确认或锁定状态）" />
                ) : (
                  <div className="gui-chat-version-change-list">
                    {selectedArtifactVersion.changes.map((change, index) => (
                      <div className="gui-chat-version-change" key={`${change.path}-${index}`}>
                        <div><Tag color={change.action === 'remove' ? 'error' : change.action === 'add' ? 'success' : 'processing'}>{change.action}</Tag><code>{change.path}</code></div>
                        {change.before !== undefined && <Text type="secondary">原：{compactVersionValue(change.before)}</Text>}
                        {change.after !== undefined && <Text>新：{compactVersionValue(change.after)}</Text>}
                      </div>
                    ))}
                  </div>
                )}
                {selectedArtifactVersion.truncated && <Alert type="warning" message="差异较多，仅显示前 500 项" />}
              </>
            ) : null}
          </div>
        </div>
      </Modal>
      <Modal
        title={`导入预览 · ${activeMaterialImport?.filename || ''}`}
        open={importPreviewOpen}
        onCancel={() => setImportPreviewOpen(false)}
        width={680}
        styles={{ body: { maxHeight: 'min(640px, calc(100vh - 250px))', overflowY: 'auto', paddingRight: 6 } }}
        footer={[
          <Button key="cancel" onClick={() => setImportPreviewOpen(false)}>稍后处理</Button>,
          <Button
            key="apply"
            type="primary"
            loading={importActionLoading}
            disabled={selectedImportArtifacts.length === 0}
            onClick={() => void applyMaterialImportPreview()}
          >
            应用所选数据
          </Button>,
        ]}
      >
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message={`已处理 ${activeMaterialImport?.processed_chunks || 0}/${activeMaterialImport?.chunk_count || 0} 个分块`}
            description="写入前可按对象选择；已确认或锁定的数据不会被静默覆盖。每条导入数据都保留原文件、分块、消息与导入运行来源。"
          />
          <div>
            <Text strong>选择导入内容</Text>
            <div style={{ marginTop: 10 }}>
              <Checkbox.Group
                className="gui-chat-import-options"
                value={selectedImportArtifacts}
                onChange={(values) => setSelectedImportArtifacts(values.map(String))}
                options={[
                  ['world_style', '文风与世界观'],
                  ['characters', '角色与关系'],
                  ['locations', '地点与势力'],
                  ['macro_outline', '主线与卷纲'],
                  ['opening_outline', '开篇细纲（需至少3章摘要）'],
                ].filter(([value]) => activeMaterialImport?.preview?.available_artifacts?.includes(value)).map(([value, label]) => ({
                  value,
                  label: `${label} · ${activeMaterialImport?.preview?.artifact_counts?.[value] || 0} 项`,
                }))}
              />
              {(activeMaterialImport?.preview?.available_artifacts?.length || 0) === 0 && (
                <Alert
                  style={{ marginTop: 10 }}
                  type="warning"
                  showIcon
                  message="没有识别到可直接写入的结构化对象"
                  description="原始文件和分块仍已保存。可以切换模型后从检查点重试，或在聊天中说明资料结构。"
                />
              )}
            </div>
          </div>
          <div>
            <Text strong>冲突处理</Text>
            <Select
              aria-label="导入冲突处理方式"
              style={{ width: '100%', marginTop: 8 }}
              value={importStrategy}
              onChange={setImportStrategy}
              options={[
                { value: 'merge', label: '与现有数据合并（推荐）' },
                { value: 'overwrite_unconfirmed', label: '覆盖未确认数据' },
                { value: 'skip_conflicts', label: '跳过所有冲突对象' },
              ]}
            />
          </div>
          {(activeMaterialImport?.preview?.conflicts?.length || 0) > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`发现 ${activeMaterialImport?.preview?.conflicts?.length} 处可能冲突`}
              description="合并会保留现有条目并追加新条目；覆盖未确认数据仍会跳过已确认或锁定内容。"
            />
          )}
        </Space>
      </Modal>
    </div>
  )
}

export default GuiAssistantChat
