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
  message,
} from 'antd'
import {
  DeleteOutlined,
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
import './GuiAssistantChat.css'

const { Title, Paragraph, Text } = Typography

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

interface ChatMessage {
  id?: string
  role: 'user' | 'assistant'
  content: string
  status?: string
  created_at?: string
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
}

interface NovelApplyData {
  project_id: string
}

type AssistantMode = 'fast' | 'quality'

const PROJECT_STORAGE_KEY = 'moshu.gui.assistant.projectId'
const SIDEBAR_STORAGE_KEY = 'moshu.gui.assistant.sidebarCollapsed'

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
      bp.golden_three?.chapter_1 ? `首章钩子：${bp.golden_three.chapter_1}` : '',
    ].filter(Boolean).join('\n')
  }).join('\n\n')
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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem(SIDEBAR_STORAGE_KEY) === '1')
  const [assistantMode, setAssistantMode] = useState<AssistantMode>('fast')
  const [temperature, setTemperature] = useState(0.3)
  const [maxTokens, setMaxTokens] = useState<number | null>(null)
  const [autoApply, setAutoApply] = useState(true)
  const [systemSessionId, setSystemSessionId] = useState<string>()
  const [systemConversationId, setSystemConversationId] = useState<string>()
  const [systemBrief, setSystemBrief] = useState('')
  const [systemBlueprints, setSystemBlueprints] = useState<NovelBlueprint[]>([])
  const [applyingBlueprintIndex, setApplyingBlueprintIndex] = useState<number | null>(null)

  const { modelOptions, defaultModel, loading: modelsLoading } = useModelOptions()
  const [model, setModel] = useState<string | undefined>()

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const activeProject = useMemo(
    () => projects.find((project) => project.id === activeProjectId),
    [projects, activeProjectId],
  )
  const assistantContextLabel = activeProject ? `作品模式 · ${activeProject.title}` : '系统模式 · 可创建新作品'

  useEffect(() => {
    if (!model && defaultModel) setModel(defaultModel)
  }, [model, defaultModel])

  useEffect(() => {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, sidebarCollapsed ? '1' : '0')
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

      const savedProjectId = localStorage.getItem(PROJECT_STORAGE_KEY) || undefined
      const nextProject = items.find((item) => item.id === savedProjectId)
      if (nextProject) {
        setActiveProjectId((current) => current || nextProject.id)
        localStorage.setItem(PROJECT_STORAGE_KEY, nextProject.id)
      } else if (savedProjectId) {
        localStorage.removeItem(PROJECT_STORAGE_KEY)
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
      fetchConversations(undefined).then((items) => {
        if (items[0]) fetchMessages(items[0].id)
      })
      return
    }
    localStorage.setItem(PROJECT_STORAGE_KEY, activeProjectId)
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

  const deleteConversation = async (convId: string) => {
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
      const reply = event.data?.reply || '已完成。'
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

  const handleSystemAssistantMessage = async (text: string) => {
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
      { role: 'user', content: text, status: 'completed', created_at: new Date().toISOString() },
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
          execution_mode: 'template',
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

      if (shouldUseNovelCreation(text, Boolean(activeProjectId))) {
        setLastAssistantMessage('收到，我先按这个设想生成三套方向不同的新书立项方案。', 'running')
        const startRes = await apiClient.post<ApiResponse<NovelStartData>>('/novel-creation/start', {
          mode: 'template',
          user_brief: text,
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
          execution_mode: 'template',
          user_brief: text,
          enhance_with_llm: false,
        })
        const blueprints = draftRes.data.data.blueprints || []
        setSystemBlueprints(blueprints)
        persistedBlueprints = blueprints
        finish(
          [
            `已生成 ${blueprints.length} 个新书方案。你可以继续提修改意见，也可以回复“使用第1个创建”。`,
            '',
            formatBlueprintSummary(blueprints),
          ].join('\n'),
        )
        return
      }

      if (systemBlueprints.length > 0 && systemSessionId && !/作品|项目|列表|有哪些|查看/.test(text)) {
        const revisionMode = /重新|全部|重来|换一批|不要当前/.test(text) ? 'regenerate' : 'refine'
        setLastAssistantMessage(
          revisionMode === 'regenerate'
            ? '我会按你的反馈重新生成整套方案。'
            : '我会在当前方案基础上继续调整书名、主角、卖点和卷纲。',
          'running',
        )
        const draftRes = await apiClient.post<ApiResponse<NovelDraftData>>('/novel-creation/draft', {
          session_id: systemSessionId,
          execution_mode: 'template',
          user_brief: systemBrief || text,
          feedback: text,
          revision_mode: revisionMode,
          enhance_with_llm: false,
        })
        const blueprints = draftRes.data.data.blueprints || []
        setSystemBlueprints(blueprints)
        persistedBlueprints = blueprints
        finish(
          [
            revisionMode === 'regenerate' ? '已重新生成 3 个方案。' : '已在当前方案基础上调整完成。',
            '你可以继续修改，或回复“使用第1个创建”。',
            '',
            formatBlueprintSummary(blueprints),
          ].join('\n'),
        )
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

      finish(
        [
          '我现在可以作为系统助手工作，不需要先进入某个作品。',
          '你可以直接说：',
          '1. “我想写1000章，克苏鲁+修仙+规则怪谈”',
          '2. “使用第1个创建”',
          '3. “查看我的作品列表”',
          '',
          activeProjectId ? '当前也已绑定作品，可以继续让我管理章节、大纲、角色和世界观。' : '当前未绑定作品，我会优先帮你创建新小说项目。',
        ].join('\n'),
      )
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

  const sendMessage = async () => {
    const text = inputValue.trim()
    if (!text || streaming) return
    const continuesSystemCreation = systemBlueprints.length > 0 && !/(写|续写|重写|查看|打开).{0,8}第?\d+\s*章/.test(text)
    if (!activeProjectId || shouldUseNovelCreation(text, Boolean(activeProjectId)) || continuesSystemCreation) {
      await handleSystemAssistantMessage(text)
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
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
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
              全部重来
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
          value={model}
          onChange={setModel}
          options={modelOptions}
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
            <Popover trigger="click" title="助手设置" content={settingsContent}>
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
                墨枢系统助手
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
                墨枢 AI 助手
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
                  <div className="gui-chat-msg-role">{msg.role === 'user' ? '你' : '墨枢'}</div>
                  <div className="gui-chat-msg-content">
                    {msg.content || (streaming && msg.role === 'assistant' ? '思考中...' : '')}
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
          <Input.TextArea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入消息...（Enter 发送，Shift+Enter 换行）"
            autoSize={{ minRows: 2, maxRows: 6 }}
            disabled={streaming}
          />
          <div className="gui-chat-composer-actions">
            {streaming ? (
              <Button icon={<StopOutlined />} onClick={stopGeneration} danger>
                停止生成
              </Button>
            ) : (
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={sendMessage}
                disabled={!inputValue.trim()}
              >
                发送
              </Button>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}

export default GuiAssistantChat
