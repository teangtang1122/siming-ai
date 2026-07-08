/**
 * Desktop control-panel assistant.
 *
 * It runs outside the project workspace page. When a project is selected it
 * uses the normal workspace assistant; without a project it behaves as a
 * system-level assistant that can help users create the first novel project.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Empty,
  Modal,
  Input,
  InputNumber,
  Popover,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd'
import {
  DeleteOutlined,
  FileAddOutlined,
  FolderOpenOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
  SettingOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { useModelOptions } from '../hooks/useModelOptions'
import {
  GLOBAL_MODEL_SELECT_VALUE,
  globalModelOptionLabel,
  readModelOverride,
  writeModelOverride,
} from '../utils/assistantModelStorage'
import './GuiAssistantChat.css'

const { Title, Paragraph, Text } = Typography
const EMPTY_ASSISTANT_REPLY = '没有收到模型的文字回复。请重试一次，或在系统设置里测试当前模型/CLI 是否支持项目助手的流式输出和工具调用。'

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

interface Conversation {
  id: string
  project_id?: string
  title: string
  scope?: string
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
  payload?: { reply?: string } | null
  created_at?: string
}

interface ChatQuestion {
  question: string
  purpose?: string
  options?: string[]
  type?: 'single_select' | 'multi_select' | 'text'
}

type QuestionAnswer = { question: string; answer: string }

function buildQuestionAnswerPayload(history: QuestionAnswer[]) {
  const answers: Record<string, string> = {}
  history.forEach((qa, index) => {
    answers[`qa_${index + 1}: ${qa.question}`] = qa.answer
  })
  return { answers, qa_history: history }
}

interface ChatMessage {
  id?: string
  role: 'user' | 'assistant'
  content: string
  status?: string
  created_at?: string
  questions?: ChatQuestion[]
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

interface NovelStartData {
  session_id: string
}

interface NovelDraftData {
  blueprints: NovelBlueprint[]
  recommendation?: string
  enhancement_mode?: 'instant_template' | 'template_llm_hybrid' | 'template_fallback' | 'llm_required'
  questions?: Array<{ question: string; purpose?: string; options?: string[] }>
  original_brief?: string
  hint?: string
}

interface NovelApplyData {
  project_id: string
}

type AssistantMode = 'fast' | 'quality'

const PROJECT_STORAGE_KEY = 'siming.gui.assistant.projectId'
const LEGACY_PROJECT_STORAGE_KEY = 'moshu.gui.assistant.projectId'
const SIDEBAR_STORAGE_KEY = 'siming.gui.assistant.sidebarCollapsed'
const LEGACY_SIDEBAR_STORAGE_KEY = 'moshu.gui.assistant.sidebarCollapsed'
const GUI_MODEL_STORAGE_KEY = 'siming.gui.assistant.model.override'
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

function parseBlueprintIndex(text: string) {
  const match = text.match(/(?:使用|选|选择|创建|就|采用|用)(?:第)?\s*([123一二三])\s*(?:个|套|号|版|方案)?/)
  if (!match) return null
  const map: Record<string, number> = { '1': 0, '2': 1, '3': 2, 一: 0, 二: 1, 三: 2 }
  return map[match[1]] ?? null
}

function formatBlueprintSummary(blueprints: NovelBlueprint[]) {
  if (!blueprints.length) return '没有生成可用方案。'
  return blueprints.map((bp, index) => {
    const protagonist = bp.protagonist?.name || '待定主角'
    const score = bp.requirement_coverage?.score
    const metrics = [
      bp.genre,
      bp.estimated_chapters ? `${bp.estimated_chapters}章` : undefined,
      `${bp.characters?.length || 0}角色`,
      `${bp.worldbuilding?.length || 0}设定`,
      score != null ? `覆盖率${score}%` : undefined,
    ].filter(Boolean).join(' / ')
    return [
      `${index + 1}. 《${bp.title}》`,
      bp.subtitle ? `方向：${bp.subtitle}` : '',
      `主角：${protagonist}`,
      metrics ? `规模：${metrics}` : '',
      bp.logline ? `一句话：${bp.logline}` : '',
      bp.protagonist?.goal ? `主角目标：${bp.protagonist.goal}` : '',
      bp.protagonist?.weakness ? `主角弱点：${bp.protagonist.weakness}` : '',
      bp.protagonist?.opening_pressure ? `开局压力：${bp.protagonist.opening_pressure}` : '',
      bp.golden_three?.chapter_1 ? `首章钩子：${bp.golden_three.chapter_1}` : '',
    ].filter(Boolean).join('\n')
  }).join('\n\n')
}

function formatDraftBlueprintMessage(draftData: NovelDraftData, successPrefix: string) {
  const blueprints = draftData.blueprints || []
  if (draftData.enhancement_mode === 'llm_required' || blueprints.length === 0) {
    return draftData.recommendation || '当前模型没有生成可用的原创新书方案，已停止模板兜底。请切换可用模型、使用外部 Agent，或明确选择模板草稿模式。'
  }
  return [successPrefix, '', formatBlueprintSummary(blueprints)].join('\n')
}

function GuiAssistantChat() {
  const [projects, setProjects] = useState<Project[]>([])
  const [activeProjectId, setActiveProjectId] = useState<string>()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConvId, setActiveConvId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [inputValue, setInputValue] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [loading, setLoading] = useState(false)
  const [projectsLoading, setProjectsLoading] = useState(false)
  const [conversationsLoading, setConversationsLoading] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => (localStorage.getItem(SIDEBAR_STORAGE_KEY) || localStorage.getItem(LEGACY_SIDEBAR_STORAGE_KEY)) === '1',
  )
  const [assistantMode, setAssistantMode] = useState<AssistantMode>('fast')
  const [temperature, setTemperature] = useState(0.3)
  const [maxTokens, setMaxTokens] = useState<number | null>(null)
  const [autoApply, setAutoApply] = useState(true)
  const [systemSessionId, setSystemSessionId] = useState<string>()
  const [systemConversationId, setSystemConversationId] = useState<string>()
  const [systemBrief, setSystemBrief] = useState('')
  const [systemBlueprints, setSystemBlueprints] = useState<NovelBlueprint[]>([])
  const [applyingBlueprintIndex, setApplyingBlueprintIndex] = useState<number | null>(null)
  const [runningStartTime, setRunningStartTime] = useState<number | null>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  // Single-question flow
  const [activeQuestion, setActiveQuestion] = useState<ChatQuestion | null>(null)
  const [questionHistory, setQuestionHistory] = useState<QuestionAnswer[]>([])
  const [selectedOption, setSelectedOption] = useState<string | null>(null)
  const [showOtherInput, setShowOtherInput] = useState(false)
  const [otherText, setOtherText] = useState('')
  const [showQAEditor, setShowQAEditor] = useState(false)
  const [editingAnswers, setEditingAnswers] = useState<Record<string, string>>({})
  const [pendingFiles, setPendingFiles] = useState<Array<{ name: string; content: string }>>([])

  const { modelOptions, defaultModel, loading: modelsLoading } = useModelOptions()
  const [model, setModel] = useState<string | undefined>(
    () => readModelOverride(GUI_MODEL_STORAGE_KEY),
  )
  const selectedModel = model || defaultModel || undefined
  const modelSelectOptions = useMemo(() => [
    { value: GLOBAL_MODEL_SELECT_VALUE, label: globalModelOptionLabel(defaultModel) },
    ...modelOptions,
  ], [defaultModel, modelOptions])
  const selectedModelValue = model || GLOBAL_MODEL_SELECT_VALUE
  const handleModelChange = (value?: string) => {
    setModel(!value || value === GLOBAL_MODEL_SELECT_VALUE ? undefined : value)
  }
  // Creative slots editor state
  const [slotEditorOpen, setSlotEditorOpen] = useState(false)
  const [slotBlueprintIndex, setSlotBlueprintIndex] = useState<number | null>(null)
  const [slotDraft, setSlotDraft] = useState<Record<string, string | string[]>>({})
  const [creationTemplates, setCreationTemplates] = useState<CreationTemplate[]>([])

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

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

  // Load creation templates on mount
  useEffect(() => {
    setCreationTemplates(readCreationTemplates())
  }, [])

  const activeProject = useMemo(
    () => projects.find((project) => project.id === activeProjectId),
    [projects, activeProjectId],
  )
  const assistantContextLabel = activeProject ? `作品模式 · ${activeProject.title}` : '系统模式 · 可创建新作品'

  useEffect(() => {
    if (model && modelOptions.length > 0 && !modelOptions.some((option) => option.value === model)) {
      setModel(undefined)
    }
  }, [model, modelOptions])

  useEffect(() => {
    writeModelOverride(GUI_MODEL_STORAGE_KEY, model)
  }, [model])

  useEffect(() => {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, sidebarCollapsed ? '1' : '0')
    localStorage.removeItem(LEGACY_SIDEBAR_STORAGE_KEY)
  }, [sidebarCollapsed])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const fetchProjects = useCallback(async () => {
    setProjectsLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<{ items: Project[]; total: number }>>('/projects')
      const items = res.data?.data?.items || []
      setProjects(items)

      const savedProjectId = localStorage.getItem(PROJECT_STORAGE_KEY) || localStorage.getItem(LEGACY_PROJECT_STORAGE_KEY) || undefined
      const nextProject = items.find((item) => item.id === savedProjectId)
      if (nextProject) {
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
  }, [])

  const fetchConversations = useCallback(async (projectId = activeProjectId) => {
    setConversationsLoading(true)
    try {
      const res = projectId
        ? await apiClient.get<ApiResponse<{ items: Conversation[]; total: number }>>(
            `/projects/${projectId}/ai/assistant/conversations`,
            { scope: 'project' },
          )
        : await apiClient.get<ApiResponse<{ items: Conversation[]; total: number }>>(
            '/ai/system-assistant/conversations',
          )
      const items = res.data?.data?.items || []
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
      const res = activeProjectId
        ? await apiClient.get<ApiResponse<{ conversation: Conversation; messages: PersistedMessage[] }>>(
            `/projects/${activeProjectId}/ai/assistant/conversations/${convId}`,
          )
        : await apiClient.get<ApiResponse<{ conversation: Conversation; messages: PersistedMessage[] }>>(
            `/ai/system-assistant/conversations/${convId}`,
          )
      const loadedMessages = (res.data?.data?.messages || []).map((item) => ({
        id: item.id,
        role: item.role,
        content: item.content || item.payload?.reply || '',
        status: item.status,
        created_at: item.created_at,
      }))
      setMessages(loadedMessages)
      setActiveConvId(res.data.data.conversation.id)
      if (!activeProjectId) {
        const conversation = res.data.data.conversation
        setSystemConversationId(conversation.id)
        setSystemSessionId(conversation.creation_session_id)
        setSystemBrief(conversation.user_brief || '')
        setSystemBlueprints(conversation.blueprints || [])
      }
    } catch (err: any) {
      message.error(err.message || '加载对话失败')
    } finally {
      setLoading(false)
    }
  }, [activeProjectId])

  useEffect(() => {
    fetchProjects()
  }, [fetchProjects])

  useEffect(() => {
    if (!activeProjectId) {
      setActiveConvId(null)
      setMessages([])
      localStorage.removeItem(PROJECT_STORAGE_KEY)
      localStorage.removeItem(LEGACY_PROJECT_STORAGE_KEY)
      fetchConversations(undefined).then((items) => {
        if (items[0]) fetchMessages(items[0].id)
      })
      return
    }
    localStorage.setItem(PROJECT_STORAGE_KEY, activeProjectId)
    localStorage.removeItem(LEGACY_PROJECT_STORAGE_KEY)
    setActiveConvId(null)
    setMessages([])
    fetchConversations(activeProjectId).then((items) => {
      if (items[0]) fetchMessages(items[0].id)
    })
  }, [activeProjectId, fetchConversations, fetchMessages])

  const startNewConversation = () => {
    abortRef.current?.abort()
    setStreaming(false)
    setActiveConvId(null)
    setMessages([])
    setInputValue('')
    setSystemSessionId(undefined)
    setSystemConversationId(undefined)
    setSystemBrief('')
    setSystemBlueprints([])
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
          if (activeProjectId) {
            await apiClient.delete(`/projects/${activeProjectId}/ai/assistant/conversations/${convId}`)
          } else {
            await apiClient.delete(`/ai/system-assistant/conversations/${convId}`)
          }
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
      }
      return [...next]
    })
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
      let conversationId = systemConversationId
      if (!conversationId) {
        const createRes = await apiClient.post<ApiResponse<{ conversation: Conversation }>>(
          '/ai/system-assistant/conversations',
          { title: userContent.slice(0, 36) },
        )
        conversationId = createRes.data.data.conversation.id
        setSystemConversationId(conversationId)
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

  const handleSystemAssistantMessage = async (text: string, originalText?: string) => {
    let finalReply = ''
    let finalStatus: ChatMessage['status'] = 'completed'
    let persistedSessionId = systemSessionId
    let persistedBrief = systemBrief
    let persistedBlueprints = systemBlueprints
    const finish = (content: string, status: ChatMessage['status'] = 'completed') => {
      finalReply = content
      finalStatus = status
      setLastAssistantMessage(content, status)
    }

    setMessages((prev) => [
      ...prev,
      { role: 'user', content: originalText || text, status: 'completed', created_at: new Date().toISOString() },
      { role: 'assistant', content: '正在处理...', status: 'running', created_at: new Date().toISOString() },
    ])
    setInputValue('')
    setStreaming(true)

    try {
      const selectedBlueprintIndex = parseBlueprintIndex(text)
      if (selectedBlueprintIndex != null && systemBlueprints[selectedBlueprintIndex]) {
        if (!systemSessionId) throw new Error('缺少新书创建会话，请重新生成方案。')
        const blueprint = systemBlueprints[selectedBlueprintIndex]
        setApplyingBlueprintIndex(selectedBlueprintIndex)
        setLastAssistantMessage(`正在创建《${blueprint.title}》...`, 'running')
        await apiClient.post<ApiResponse<unknown>>('/novel-creation/review', {
          session_id: systemSessionId,
          execution_mode: 'hybrid',
          blueprint: systemBlueprints,
        })
        const applyRes = await apiClient.post<ApiResponse<NovelApplyData>>('/novel-creation/apply', {
          session_id: systemSessionId,
          blueprint_index: selectedBlueprintIndex,
          mode: 'auto',
          blueprint,
        })
        const projectId = applyRes.data.data.project_id
        await fetchProjects()
        setActiveProjectId(projectId)
        localStorage.setItem(PROJECT_STORAGE_KEY, projectId)
        setSystemBlueprints([])
        setSystemSessionId(undefined)
        setSystemBrief('')
        persistedBlueprints = []
        persistedSessionId = undefined
        persistedBrief = ''
        finish(
          `已创建新作品《${blueprint.title}》。我已经切换到这个作品上下文，接下来可以继续让我细化大纲、角色、世界观，或直接开始写第一章。`,
        )
        return
      }

      if (shouldUseNovelCreation(originalText || text, Boolean(activeProjectId))) {
        setLastAssistantMessage('收到，正在分析你的需求...', 'running')
        const startRes = await apiClient.post<ApiResponse<NovelStartData>>('/novel-creation/start', {
          mode: 'template',
          user_brief: text, // This includes file content if imported
          genre: '',
          target_audience: '',
          platform: '',
        })
        const sessionId = startRes.data.data.session_id
        setSystemSessionId(sessionId)
        setSystemBrief(text)
        persistedSessionId = sessionId
        persistedBrief = text
        const draftRes = await apiClient.post<ApiResponse<NovelDraftData>>('/novel-creation/draft', {
          session_id: sessionId,
          execution_mode: 'hybrid',
          model: selectedModel,
          user_brief: text,
          enhance_with_llm: false,
        })
        const draftData = draftRes.data.data

        // Handle clarifying questions — show first question only
        if (draftData.questions && draftData.questions.length > 0) {
          const firstQ = draftData.questions[0]
          setActiveQuestion(firstQ)
          setCurrentOptions(firstQ.options || [])
          setQuestionHistory([])
          setSelectedOption(null)
          setShowOtherInput(false)
          setOtherText('')
          setRunningStartTime(null)
          setMessages((prev) => [
            ...prev,
            { role: 'assistant', content: '', questions: [firstQ], status: 'completed' },
          ])
          return
        }

        const blueprints = draftData.blueprints || []
        setSystemBlueprints(blueprints)
        persistedBlueprints = blueprints
        finish(
          formatDraftBlueprintMessage(
            draftData,
            `已生成 ${blueprints.length} 个新书方案。你可以继续提修改意见，也可以回复”使用第1个创建”。`,
          ),
        )
        return
      }

      // Handle answers to clarifying questions (session exists but no blueprints yet)
      if (systemSessionId && systemBlueprints.length === 0 && !/作品|项目|列表|有哪些|查看/.test(text)) {
        const isSkip = /跳过|不用了|直接生成|skip/i.test(text)
        if (isSkip) {
          // Skip questions — generate directly
          setLastAssistantMessage('收到，正在生成方案，预计需要30-60秒...', 'running')
          setActiveQuestion(null)
          setSelectedOption(null)
          setShowOtherInput(false)
          setOtherText('')
          const draftRes = await apiClient.post<ApiResponse<NovelDraftData>>('/novel-creation/draft', {
            session_id: systemSessionId,
            execution_mode: 'hybrid',
            model: selectedModel,
            user_brief: systemBrief || text,
            skip_questions: true,
            revision_mode: 'initial',
          })
          const draftData = draftRes.data.data
          const blueprints = draftData.blueprints || []
          setSystemBlueprints(blueprints)
          finish(
            formatDraftBlueprintMessage(
              draftData,
              `已生成 ${blueprints.length} 个新书方案。你可以继续提修改意见，也可以回复"使用第1个创建"。`,
            ),
          )
        } else if (activeQuestion) {
          // User typed an answer while a question is active — use submitQuestionAnswer
          submitQuestionAnswer(text)
        } else {
          // No active question but session exists — treat as brief supplement
          setRunningStartTime(Date.now())
          setLastAssistantMessage('思考中...', 'running')
          const newHistory = [...questionHistory, { question: '用户补充', answer: text }]
          const qaPayload = buildQuestionAnswerPayload(newHistory)
          const draftRes = await apiClient.post<ApiResponse<NovelDraftData>>('/novel-creation/draft', {
            session_id: systemSessionId,
            execution_mode: 'hybrid',
            model: selectedModel,
            user_brief: systemBrief || text,
            ...qaPayload,
            revision_mode: 'initial',
          })
          const draftData = draftRes.data.data
          if (draftData.questions && draftData.questions.length > 0) {
            const nextQ = draftData.questions[0]
            setActiveQuestion(nextQ)
            setQuestionHistory(newHistory)
            setRunningStartTime(null)
            setMessages((prev) => [
              ...prev,
              { role: 'assistant', content: '', questions: [nextQ], status: 'completed' },
            ])
          } else {
            setActiveQuestion(null)
            const blueprints = draftData.blueprints || []
            setSystemBlueprints(blueprints)
            finish(
              formatDraftBlueprintMessage(
                draftData,
                `已生成 ${blueprints.length} 个新书方案。你可以继续提修改意见，也可以回复"使用第1个创建"。`,
              ),
            )
          }
        }
        return
      }

      if (systemBlueprints.length > 0 && systemSessionId && !/作品|项目|列表|有哪些|查看/.test(text)) {
        const revisionMode = /重新|全部|重来|换一批|不要当前|换[别的]|不好|不满意|其他方案|不同/.test(text) ? 'regenerate' : 'refine'
        setLastAssistantMessage(
          revisionMode === 'regenerate'
            ? '正在按反馈重新生成整套方案，预计需要30-60秒...'
            : '正在用LLM调整方案，预计需要20-40秒...',
          'running',
        )
        const draftRes = await apiClient.post<ApiResponse<NovelDraftData>>('/novel-creation/draft', {
          session_id: systemSessionId,
          execution_mode: 'hybrid',
          model: selectedModel,
          user_brief: systemBrief || text,
          feedback: text,
          revision_mode: revisionMode,
          enhance_with_llm: true,
        })
        const draftData = draftRes.data.data
        const blueprints = draftData.blueprints || []
        setSystemBlueprints(blueprints)
        persistedBlueprints = blueprints
        if (draftData.enhancement_mode === 'llm_required' || blueprints.length === 0) {
          finish(formatDraftBlueprintMessage(draftData, ''))
        } else {
          finish(
            [
              revisionMode === 'regenerate' ? '已重新生成 3 个方案。' : '已在当前方案基础上调整完成。',
              '本轮已由模型生成原创调整结果。',
              '你可以继续修改，或回复“使用第1个创建”。',
              '',
              formatBlueprintSummary(blueprints),
            ].join('\n'),
          )
        }
        return
      }

      if (/作品|项目|列表|有哪些|查看/.test(text)) {
        const res = await apiClient.get<ApiResponse<{ items: Project[]; total: number }>>('/projects')
        const items = res.data?.data?.items || []
        setProjects(items)
        if (!items.length) {
          finish('当前还没有作品。你可以直接说“我想写克苏鲁+修仙+规则怪谈”，我会先生成三套新书方案。')
          return
        }
        const projectList = items.map((project, index) => `${index + 1}. ${project.title}`).join('\n')
        finish(`当前共有 ${items.length} 个作品：\n${projectList}\n\n你可以在助手设置里切换作品，也可以直接让我创建新作品。`)
        return
      }

      // LLM-powered natural conversation fallback
      try {
        const chatRes = await apiClient.post<ApiResponse<{ reply: string }>>('/novel-creation/system-chat', {
          message: text,
          model: selectedModel,
          context: {
            blueprints: systemBlueprints,
            sessionId: systemSessionId,
            brief: systemBrief,
            importedFiles: pendingFiles.map(f => ({ name: f.name, length: f.content.length })),
            history: messages.slice(-6).map(m => ({ role: m.role, content: m.content })),
          },
        })
        finish(chatRes.data.data.reply)
      } catch {
        finish('抱歉，出了点问题。你可以直接说”我想写一本新书”来开始创作。')
      }
    } catch (err: any) {
      finish(err.message || '处理失败', 'error')
      message.error(err.message || '处理失败')
    } finally {
      if (finalReply) {
        await persistSystemTurn(text, finalReply, finalStatus, {
          creationSessionId: persistedSessionId,
          userBrief: persistedBrief,
          blueprints: persistedBlueprints,
        })
      }
      setStreaming(false)
      setApplyingBlueprintIndex(null)
    }
  }

  const handleFileImport = async (file: File) => {
    try {
      // Read file client-side
      const text = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(reader.result as string)
        reader.onerror = () => reject(new Error('文件读取失败'))
        reader.readAsText(file, 'utf-8')
      })

      // Add to pending files list (don't send yet)
      setPendingFiles((prev) => [...prev, { name: file.name, content: text }])
      message.success(`已添加文件「${file.name}」（${text.length}字）`)
    } catch {
      message.error('文件读取失败，请确认文件格式正确后重试。')
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
            📎 {f.name} ({f.content.length}字)
          </Tag>
        ))}
      </div>
    )
  }

  // Import file directly as a new project (skip novel creation flow)
  const handleFileImportAsProject = async (file: { name: string; content: string }, userText: string) => {
    setMessages((prev) => [...prev, { role: 'user', content: userText }])
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

      // Import the file content as chapters
      // Split by chapter markers
      const chapterPattern = /^(第[一二三四五六七八九十百千\d]+[章节回卷]|Chapter\s+\d+)/m
      const parts = file.content.split(chapterPattern).filter(Boolean)

      // If no chapter markers found, treat the whole file as one chapter
      const chapters = []
      if (parts.length <= 1) {
        chapters.push({ title: '正文', content: file.content })
      } else {
        for (let i = 0; i < parts.length; i += 2) {
          const title = parts[i]?.trim() || `第${Math.floor(i / 2) + 1}章`
          const content = parts[i + 1]?.trim() || ''
          if (content) {
            chapters.push({ title, content })
          }
        }
      }

      // Create chapters via API
      for (let i = 0; i < chapters.length; i++) {
        const ch = chapters[i]
        await apiClient.post(`/projects/${projectId}/chapters`, {
          title: ch.title,
          content: ch.content,
          order: i,
        })
      }

      await fetchProjects()
      setActiveProjectId(projectId)
      localStorage.setItem(PROJECT_STORAGE_KEY, projectId)

      setMessages((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last?.role === 'assistant' && last?.status === 'running') {
          last.content = `已创建作品「${title}」并导入 ${chapters.length} 章（${file.content.length}字）。已切换到该作品上下文，可以继续编辑。`
          last.status = 'completed'
        }
        return [...next]
      })
      setStreaming(false)
    } catch {
      setMessages((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last?.role === 'assistant' && last?.status === 'running') {
          last.content = '导入失败，请重试。'
          last.status = 'error'
        }
        return [...next]
      })
      setStreaming(false)
    }
  }

  const sendMessage = async () => {
    const text = inputValue.trim()
    // Allow sending if there are pending files (even without text)
    if ((!text && pendingFiles.length === 0) || streaming) return
    // If only files without text, use a default message
    const effectiveText = text || '请帮我处理这些文件'

    // Detect if user wants to import files as a new project
    const isImportAsProject = pendingFiles.length > 0 && /导入|作为新作品|创建为新作品|导入为新作品/.test(text)

    if (isImportAsProject && pendingFiles.length === 1) {
      await handleFileImportAsProject(pendingFiles[0], text)
      setPendingFiles([])
      return
    }

    // Build message with file context
    let messageWithContext = effectiveText
    const fileNames = pendingFiles.map(f => f.name) // Save before clearing
    if (pendingFiles.length > 0) {
      // Save files to backend working directory
      const savedPaths: string[] = []
      for (const f of pendingFiles) {
        try {
          const res = await apiClient.post<ApiResponse<{ path: string }>>('/novel-creation/save-imported-file', {
            filename: f.name,
            content: f.content,
          })
          savedPaths.push(res.data.data.path)
        } catch {
          // If save fails, include content inline
          savedPaths.push('')
        }
      }

      // Build context with file references
      const fileContexts = pendingFiles.map((f, i) => {
        const pathNote = savedPaths[i] ? ` (已保存到: ${savedPaths[i]})` : ''
        return `[参考文件${i + 1}：${f.name}${pathNote}]\n${f.content.slice(0, 4000)}${f.content.length > 4000 ? '\n...(已截断)' : ''}`
      })

      messageWithContext = [
        ...fileContexts,
        '',
        `用户指令：${effectiveText}`,
      ].join('\n\n')
      setPendingFiles([])
    }

    // Build display text (includes file names if files were attached)
    const displayText = text || (fileNames.length > 0 ? `📎 ${fileNames.join(', ')}` : '')

    const continuesSystemCreation = systemBlueprints.length > 0 && !/(写|续写|重写|查看|打开).{0,8}第?\d+\s*章/.test(text)
    if (!activeProjectId || shouldUseNovelCreation(text, Boolean(activeProjectId)) || continuesSystemCreation) {
      await handleSystemAssistantMessage(messageWithContext, displayText)
      return
    }

    const history = messages.slice(-8).map((item) => ({
      role: item.role,
      content: item.content,
    }))

    setMessages((prev) => [
      ...prev,
      { role: 'user', content: text, status: 'completed', created_at: new Date().toISOString() },
      { role: 'assistant', content: '正在分析需求...', status: 'running', created_at: new Date().toISOString() },
    ])
    setInputValue('')
    setStreaming(true)

    try {
      abortRef.current = new AbortController()
      const res = await fetch(`/api/v1/projects/${activeProjectId}/ai/workspace-assistant/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scope: 'project',
          message: text,
          conversation_id: activeConvId || undefined,
          model: model || defaultModel || undefined,
          assistant_mode: assistantMode,
          temperature,
          max_tokens: maxTokens || undefined,
          auto_apply: autoApply,
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
          handleSseEvent(JSON.parse(data))
        }
      }

      buffer += decoder.decode()
      if (buffer.trim()) {
        const data = buffer
          .split(/\r?\n/)
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.replace(/^data:\s?/, ''))
          .join('\n')
        if (data && data !== '[DONE]') handleSseEvent(JSON.parse(data))
      }
      fetchConversations(activeProjectId)
    } catch (err: any) {
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
    }
  }

  const stopGeneration = () => {
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

  // ── Single-question interactive flow ──
  const submitQuestionAnswer = async (answer: string) => {
    if (!activeQuestion || !systemSessionId) return

    // Add user's answer to chat
    setMessages((prev) => [...prev, { role: 'user', content: answer }])

    // Update history
    const newHistory = [...questionHistory, { question: activeQuestion.question, answer }]
    setQuestionHistory(newHistory)

    // Clear current question state
    setActiveQuestion(null)
    setSelectedOption(null)
    setShowOtherInput(false)
    setOtherText('')
    setCurrentOptions([])

    // Show thinking indicator with timer
    setRunningStartTime(Date.now())
    setMessages((prev) => [
      ...prev,
      { role: 'assistant', content: '思考中...', status: 'running' },
    ])

    try {
      const qaPayload = buildQuestionAnswerPayload(newHistory)

      const draftRes = await apiClient.post<ApiResponse<NovelDraftData>>('/novel-creation/draft', {
        session_id: systemSessionId,
        execution_mode: 'hybrid',
        model: selectedModel,
        user_brief: systemBrief,
        ...qaPayload,
        revision_mode: 'initial',
      })
      const draftData = draftRes.data.data

      if (draftData.questions && draftData.questions.length > 0) {
        // More questions — replace thinking message with question card
        const nextQ = draftData.questions[0]
        setActiveQuestion(nextQ)
        setCurrentOptions(nextQ.options || [])
        setSelectedOption(null)
        setShowOtherInput(false)
        setOtherText('')
        setRunningStartTime(null)
        setMessages((prev) => {
          const next = [...prev]
          // Update the last "thinking" message to show the question
          const last = next[next.length - 1]
          if (last?.role === 'assistant' && last?.status === 'running') {
            last.content = ''
            last.questions = [nextQ]
            last.status = 'completed'
          }
          return [...next]
        })
      } else {
        // Info sufficient — replace thinking message with blueprints
        setActiveQuestion(null)
        const blueprints = draftData.blueprints || []
        setSystemBlueprints(blueprints)
        setRunningStartTime(null)
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last?.role === 'assistant' && last?.status === 'running') {
            last.content = formatDraftBlueprintMessage(
              draftData,
              `已生成 ${blueprints.length} 个新书方案。你可以继续提修改意见，也可以回复"使用第1个创建"。`,
            )
            last.questions = undefined
            last.status = 'completed'
          }
          return [...next]
        })
      }
    } catch {
      setActiveQuestion(null)
      setRunningStartTime(null)
      setMessages((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last?.role === 'assistant' && last?.status === 'running') {
          last.content = '网络连接出现问题，请重试。'
          last.status = 'error'
        }
        return [...next]
      })
    }
  }

  const handleQuestionSkip = async () => {
    if (!systemSessionId) return
    setRunningStartTime(Date.now())
    setLastAssistantMessage('收到，正在生成方案，预计需要30-60秒...', 'running')
    setActiveQuestion(null)
    setSelectedOption(null)
    setShowOtherInput(false)
    setOtherText('')

    try {
      const draftRes = await apiClient.post<ApiResponse<NovelDraftData>>('/novel-creation/draft', {
        session_id: systemSessionId,
        execution_mode: 'hybrid',
        model: selectedModel,
        user_brief: systemBrief,
        skip_questions: true,
        revision_mode: 'initial',
      })
      const draftData = draftRes.data.data
      const blueprints = draftData.blueprints || []
      setSystemBlueprints(blueprints)
      setRunningStartTime(null)
      setLastAssistantMessage(
        formatDraftBlueprintMessage(
          draftData,
          `已生成 ${blueprints.length} 个新书方案。你可以继续提修改意见，也可以回复"使用第1个创建"。`,
        ),
        'completed',
      )
    } catch {
      setRunningStartTime(null)
      setLastAssistantMessage('生成失败，请重试。', 'completed')
    }
  }

  const [currentOptions, setCurrentOptions] = useState<string[]>([])
  const [isRefreshing, setIsRefreshing] = useState(false)

  const handleReplaceOption = async (optionToReplace: string) => {
    if (!activeQuestion || !systemSessionId) return
    setIsRefreshing(true)
    try {
      const res = await apiClient.post<ApiResponse<{ question: string; options: string[] }>>(
        '/novel-creation/refresh-question',
        {
          session_id: systemSessionId,
          question: activeQuestion.question,
          existing_options: currentOptions,
          user_brief: systemBrief,
          model: selectedModel,
        },
      )
      const newOptions = res.data?.data?.options || []
      if (newOptions.length > 0) {
        // Replace the selected option with the new one
        setCurrentOptions((prev) => prev.map((o) => o === optionToReplace ? newOptions[0] : o))
        setActiveQuestion((prev) => prev ? {
          ...prev,
          options: (prev.options || []).map((o) => o === optionToReplace ? newOptions[0] : o),
        } : null)
        setSelectedOption(newOptions[0])
      }
    } catch {
      // silent fail
    } finally {
      setIsRefreshing(false)
    }
  }

  const renderQuestions = (questions: ChatQuestion[]) => {
    // Only render the first question (single-question flow)
    const q = questions[0]
    if (!q) return null

    const displayOptions = currentOptions.length > 0 ? currentOptions : (q.options || [])

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: '8px 0' }}>
        <div style={{ background: '#f8f9fa', borderRadius: 8, padding: 12 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 8 }}>
            <span style={{ fontWeight: 600, color: '#1890ff' }}>Q</span>
            <span>{q.question}</span>
            {q.purpose && <span style={{ fontSize: 12, color: '#999' }}>{q.purpose}</span>}
          </div>
          {q.type === 'text' ? (
            <div>
              <Input.TextArea
                placeholder="请输入你的回答..."
                autoSize={{ minRows: 1, maxRows: 3 }}
                value={otherText}
                onChange={(e) => setOtherText(e.target.value)}
                onPressEnter={(e) => {
                  if (!e.shiftKey && otherText.trim()) {
                    e.preventDefault()
                    submitQuestionAnswer(otherText.trim())
                  }
                }}
              />
              <Button
                type="primary"
                size="small"
                style={{ marginTop: 8 }}
                disabled={!otherText.trim()}
                onClick={() => submitQuestionAnswer(otherText.trim())}
              >
                确认回答
              </Button>
            </div>
          ) : (
            <div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
                {displayOptions.map((opt, oi) => (
                  <Button
                    key={oi}
                    type={selectedOption === opt ? 'primary' : 'default'}
                    size="small"
                    onClick={() => {
                      setSelectedOption(opt)
                      setShowOtherInput(false)
                      setOtherText('')
                    }}
                  >
                    {opt}
                  </Button>
                ))}
              </div>
              <div style={{ marginBottom: 8 }}>
                <Button
                  type={showOtherInput ? 'primary' : 'dashed'}
                  size="small"
                  block
                  onClick={() => {
                    setShowOtherInput(true)
                    setSelectedOption(null)
                  }}
                >
                  其他...
                </Button>
              </div>
              {/* Action buttons — show when an option is selected */}
              {selectedOption && !showOtherInput && (
                <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                  <Button
                    type="primary"
                    size="small"
                    onClick={() => submitQuestionAnswer(selectedOption)}
                  >
                    确认回答
                  </Button>
                  <Button
                    size="small"
                    loading={isRefreshing}
                    onClick={() => handleReplaceOption(selectedOption)}
                  >
                    替换该选项
                  </Button>
                </div>
              )}
            </div>
          )}
          {showOtherInput && q.type !== 'text' && (
            <div style={{ marginTop: 8 }}>
              <Input.TextArea
                placeholder="请输入你的回答..."
                autoSize={{ minRows: 1, maxRows: 3 }}
                value={otherText}
                onChange={(e) => setOtherText(e.target.value)}
                onPressEnter={(e) => {
                  if (!e.shiftKey && otherText.trim()) {
                    e.preventDefault()
                    submitQuestionAnswer(otherText.trim())
                  }
                }}
              />
              <Button
                type="primary"
                size="small"
                style={{ marginTop: 4 }}
                disabled={!otherText.trim()}
                onClick={() => submitQuestionAnswer(otherText.trim())}
              >
                确认回答
              </Button>
            </div>
          )}
        </div>
        <Button type="link" onClick={handleQuestionSkip} style={{ alignSelf: 'flex-start' }}>
          跳过，直接生成
        </Button>
      </div>
    )
  }

  // ── Regenerate with current Q&A history ──
  const handleRegenerateWithAnswers = async (updatedHistory?: QuestionAnswer[]) => {
    if (!systemSessionId) return
    const history = updatedHistory || questionHistory
    if (history.length === 0) {
      // No Q&A history, use the old flow
      handleSystemAssistantMessage('全部重新生成')
      return
    }

    setRunningStartTime(Date.now())
    setSystemBlueprints([])
    setShowQAEditor(false)
    setMessages((prev) => [
      ...prev,
      { role: 'assistant', content: '思考中...', status: 'running' },
    ])

    try {
      const qaPayload = buildQuestionAnswerPayload(history)

      const draftRes = await apiClient.post<ApiResponse<NovelDraftData>>('/novel-creation/draft', {
        session_id: systemSessionId,
        execution_mode: 'hybrid',
        model: selectedModel,
        user_brief: systemBrief,
        ...qaPayload,
        revision_mode: 'initial',
      })
      const draftData = draftRes.data.data

      if (draftData.questions && draftData.questions.length > 0) {
        const nextQ = draftData.questions[0]
        setActiveQuestion(nextQ)
        setCurrentOptions(nextQ.options || [])
        setQuestionHistory(history)
        setRunningStartTime(null)
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last?.role === 'assistant' && last?.status === 'running') {
            last.content = ''
            last.questions = [nextQ]
            last.status = 'completed'
          }
          return [...next]
        })
      } else {
        const blueprints = draftData.blueprints || []
        setSystemBlueprints(blueprints)
        setRunningStartTime(null)
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last?.role === 'assistant' && last?.status === 'running') {
            last.content = formatDraftBlueprintMessage(
              draftData,
              `已重新生成 ${blueprints.length} 个方案。`,
            )
            last.status = 'completed'
          }
          return [...next]
        })
      }
    } catch {
      setRunningStartTime(null)
      setMessages((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last?.role === 'assistant' && last?.status === 'running') {
          last.content = '网络连接出现问题，请重试。'
          last.status = 'error'
        }
        return [...next]
      })
    }
  }

  const renderQAEditor = () => {
    if (!showQAEditor || questionHistory.length === 0) return null
    return (
      <div style={{ background: '#f8f9fa', borderRadius: 8, padding: 12, marginBottom: 8 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>修改你的回答：</div>
        {questionHistory.map((qa, i) => (
          <div key={i} style={{ marginBottom: 8 }}>
            <div style={{ fontSize: 12, color: '#666', marginBottom: 4 }}>{qa.question}</div>
            <Input
              size="small"
              value={editingAnswers[String(i)] ?? qa.answer}
              onChange={(e) => setEditingAnswers((prev) => ({ ...prev, [String(i)]: e.target.value }))}
            />
          </div>
        ))}
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <Button
            type="primary"
            size="small"
            onClick={() => {
              // Apply edits and regenerate
              const updatedHistory = questionHistory.map((qa, i) => ({
                question: qa.question,
                answer: editingAnswers[String(i)] ?? qa.answer,
              }))
              setQuestionHistory(updatedHistory)
              handleRegenerateWithAnswers(updatedHistory)
            }}
          >
            修改后重新生成
          </Button>
          <Button size="small" onClick={() => setShowQAEditor(false)}>
            取消
          </Button>
        </div>
      </div>
    )
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
          background: '#fff', borderRadius: 8, padding: 24, width: 600,
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
        {renderQAEditor()}
        <div className="gui-chat-blueprints-head">
          <div>
            <Text strong>新书方案</Text>
            <Text type="secondary">  ·  可继续对话微调，也可以直接创建</Text>
          </div>
          <Space size={8}>
            {questionHistory.length > 0 && (
              <Button size="small" onClick={() => {
                setEditingAnswers({})
                setShowQAEditor(!showQAEditor)
              }}>
                修改回答
              </Button>
            )}
            <Button size="small" onClick={() => {
              if (questionHistory.length > 0) {
                handleRegenerateWithAnswers()
              } else {
                handleSystemAssistantMessage('全部重新生成')
              }
            }} disabled={streaming}>
              重新生成
            </Button>
            <Button size="small" onClick={() => handleSystemAssistantMessage('强化书名、主角动机和前三章钩子')} disabled={streaming}>
              强化方案
            </Button>
          </Space>
        </div>
        <div className="gui-chat-blueprint-grid">
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
                    loading={applyingBlueprintIndex === index}
                    disabled={streaming && applyingBlueprintIndex !== index}
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

  const quickActions = [
    {
      label: '新书立项',
      prompt: '帮我创建一本新的小说，克苏鲁+规则怪谈，至少要能写1000章的创意',
    },
    {
      label: '查看作品',
      prompt: '查看我的作品列表',
    },
    {
      label: activeProject ? '规划后续' : '设计主角',
      prompt: activeProject ? '根据当前作品，帮我规划后续三章' : '分别设计一下主角，给出名字、目标、弱点和开局压力',
    },
  ]

  const settingsContent = (
    <Space direction="vertical" size={12} style={{ width: 300 }}>
      <div>
        <Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>作品上下文</Text>
        <Select
          showSearch
          value={activeProjectId}
          onChange={setActiveProjectId}
          options={projects.map((project) => ({ value: project.id, label: project.title }))}
          loading={projectsLoading}
          allowClear
          placeholder="可不选作品，直接创建新书"
          optionFilterProp="label"
          style={{ width: '100%' }}
        />
      </div>
      <div>
        <Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>模型</Text>
        <Select
          allowClear
          showSearch
          value={selectedModelValue}
          onChange={handleModelChange}
          options={modelSelectOptions}
          loading={modelsLoading}
          optionFilterProp="label"
          placeholder={modelOptions.length ? '选择模型' : '请先在系统设置里配置模型'}
          style={{ width: '100%' }}
        />
      </div>
      <div>
        <Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>助手模式</Text>
        <Select
          value={assistantMode}
          onChange={setAssistantMode}
          options={[
            { value: 'fast', label: '快速（默认）' },
            { value: 'quality', label: '质量' },
          ]}
          style={{ width: '100%' }}
        />
      </div>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Text type="secondary">自动执行工具</Text>
        <Switch checked={autoApply} onChange={setAutoApply} />
      </Space>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Text type="secondary">温度</Text>
        <InputNumber
          min={0}
          max={2}
          step={0.1}
          value={temperature}
          onChange={(value) => setTemperature(Number(value ?? 0.3))}
          style={{ width: 110 }}
        />
      </Space>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Text type="secondary">最大输出</Text>
        <InputNumber
          min={256}
          step={256}
          value={maxTokens ?? undefined}
          placeholder="默认"
          onChange={(value) => setMaxTokens(value ? Number(value) : null)}
          style={{ width: 110 }}
        />
      </Space>
    </Space>
  )

  return (
    <div className={`gui-chat${sidebarCollapsed ? ' gui-chat-collapsed' : ''}`}>
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
          <div style={{ minWidth: 0 }}>
            <Title level={5} style={{ margin: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {activeConvId ? conversations.find((c) => c.id === activeConvId)?.title || 'AI 助手' : 'AI 助手'}
            </Title>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
              <Tag color={activeProject ? 'blue' : 'purple'} style={{ margin: 0 }}>{assistantContextLabel}</Tag>
              <Text type="secondary" className="gui-chat-project-line" style={{ marginTop: 0 }}>
                <FolderOpenOutlined /> {activeProject ? '作品问题会写回当前作品；新书/创建意图会自动进入系统立项流程' : '未选择作品时仍可创建新小说'}
              </Text>
            </div>
          </div>
          <Space>
            <Button onClick={() => setInputValue('帮我创建一本新的小说，克苏鲁+规则怪谈，至少要能写1000章的创意')}>
              新书立项
            </Button>
            <Popover
              trigger="click"
              title="助手设置"
              content={settingsContent}
              onOpenChange={(open) => { if (open) fetchProjects() }}
            >
              <Button icon={<SettingOutlined />}>助手设置</Button>
            </Popover>
            <Button icon={<PlusOutlined />} onClick={startNewConversation}>
              新对话
            </Button>
          </Space>
        </div>

        <div className="gui-chat-messages">
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
                <Button type="primary" icon={<PlusOutlined />} size="large" onClick={() => setInputValue('我想写1000章，克苏鲁+修仙+规则怪谈')}>
                  试着创建新小说
                </Button>
                <Button size="large" onClick={() => setInputValue('查看我的作品列表')}>
                  查看作品列表
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
                <div key={msg.id || `${msg.role}-${index}`} className={`gui-chat-msg gui-chat-msg-${msg.role}`}>
                  <div className="gui-chat-msg-role">{msg.role === 'user' ? '你' : '司命'}</div>
                  <div className="gui-chat-msg-content">
                    {msg.content || (streaming && msg.role === 'assistant' ? '思考中...' : '')}
                    {msg.status === 'running' && elapsedSeconds > 0 && (
                      <span style={{ color: '#999', fontSize: 12, marginLeft: 8 }}>
                        ⏱ {elapsedSeconds}s
                      </span>
                    )}
                    {msg.questions && msg.questions.length > 0 && renderQuestions(msg.questions)}
                  </div>
                </div>
              ))}
              {renderBlueprintCards()}
              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        <div className="gui-chat-composer">
          <div className="gui-chat-quick-actions">
            {quickActions.map((action) => (
              <Button
                key={action.label}
                size="small"
                onClick={() => setInputValue(action.prompt)}
                disabled={streaming}
              >
                {action.label}
              </Button>
            ))}
          </div>
          {renderPendingFiles()}
          <Input.TextArea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={pendingFiles.length > 0 ? '描述你想怎么处理这些文件...' : '输入消息...（Enter 发送，Shift+Enter 换行）'}
            autoSize={{ minRows: 2, maxRows: 6 }}
            disabled={streaming}
          />
          <div className="gui-chat-composer-actions">
            {streaming ? (
              <Button icon={<StopOutlined />} onClick={stopGeneration} danger>
                停止生成
              </Button>
            ) : (
              <>
                <Upload
                  accept=".txt,.docx"
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
                  onClick={sendMessage}
                  disabled={!inputValue.trim() && pendingFiles.length === 0}
                >
                  发送
                </Button>
              </>
            )}
          </div>
        </div>
      </main>
      {renderSlotEditorModal()}
    </div>
  )
}

export default GuiAssistantChat
