import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Input, InputNumber, Modal, Popover, Select, Space, Switch, Tag, Tooltip, Typography, message } from 'antd'
import {
  DeleteOutlined,
  InfoCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import AgentPlanView, { type AgentPlanViewState, type AgentPlanStepView } from './AgentPlanView'
import { AssistantMemoryModal } from './AssistantMemoryModal'
import {
  type ApiResponse,
  type WorkspaceAssistantChatProps,
  type WorkspaceAssistantConversation,
  type WorkspaceAssistantMessage,
  type WorkspaceAssistantMode,
  type WorkspaceAssistantResponse,
  type WorkspaceAssistantRun,
  type WorkspaceAssistantRunDetail,
  type WorkspacePersistedMessage,
  type WorkspaceRunLog,
  type WorkspaceToolLog,
  type SkillMatch,
  type StepDetail,
  DEFAULT_FORBIDDEN_SENTENCE_PATTERNS,
  DEFAULT_RHETORIC_GUIDELINES,
  SCOPE_LABEL,
  mergeForbiddenPatterns,
  stripDefaults,
  createEmptyWorkspaceResponse,
  runStepToLog,
  sortWorkspaceMessages,
  toWorkspaceMessage,
  MessageList,
  Composer,
  StepDetailModal,
} from './assistant'
import { GLOBAL_MODEL_SELECT_VALUE, globalModelOptionLabel } from '../utils/assistantModelStorage'
import './WorkspaceAssistantChat.css'

const { Text } = Typography
const { TextArea } = Input
const EMPTY_ASSISTANT_REPLY = '没有收到模型的文字回复。请重试一次，或在系统设置里测试当前模型/CLI 是否支持项目助手的流式输出和工具调用。'

function WorkspaceAssistantChat({
  projectId,
  scope,
  selectedOutlineNodeId,
  selectedCharacterId,
  selectedText,
  selectedTextChapterId,
  model,
  defaultModel,
  modelOptions,
  modelsLoading,
  onModelChange,
  onApplied,
}: WorkspaceAssistantChatProps) {
  const [conversations, setConversations] = useState<WorkspaceAssistantConversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<WorkspaceAssistantMessage[]>([])
  const [input, setInput] = useState('')
  const [generating, setGenerating] = useState(false)

  const [historyLoading, setHistoryLoading] = useState(false)
  const [runLogs, setRunLogs] = useState<WorkspaceRunLog[]>([])
  const [currentRun, setCurrentRun] = useState<WorkspaceAssistantRun | null>(null)
  const [temperature, setTemperature] = useState(0.3)
  const [maxTokens, setMaxTokens] = useState<number | null>(null)
  const [autoApply, setAutoApply] = useState(true)
  const [assistantMode, setAssistantMode] = useState<WorkspaceAssistantMode>('fast')
  const [showAllRunLogs, setShowAllRunLogs] = useState(false)
  const [showSelectionTag, setShowSelectionTag] = useState(true)
  const [retryingStepId, setRetryingStepId] = useState<string | null>(null)
  const [currentPlan, setCurrentPlan] = useState<AgentPlanViewState | null>(null)
  const [retryingPlanKey, setRetryingPlanKey] = useState<string | null>(null)
  const [detailStep, setDetailStep] = useState<StepDetail | null>(null)
  const modelSelectOptions = useMemo(() => [
    { value: GLOBAL_MODEL_SELECT_VALUE, label: globalModelOptionLabel(defaultModel) },
    ...modelOptions,
  ], [defaultModel, modelOptions])
  const selectedModelValue = model || GLOBAL_MODEL_SELECT_VALUE
  const handleModelChange = (value?: string) => {
    onModelChange?.(!value || value === GLOBAL_MODEL_SELECT_VALUE ? undefined : value)
  }

  useEffect(() => {
    setShowSelectionTag(true)
  }, [selectedText])
  const [outlineBatchCount, setOutlineBatchCount] = useState(3)
  const [narrativePerspective, setNarrativePerspective] = useState('third_person')
  const [writingStyle, setWritingStyle] = useState('natural')
  const [forbiddenSentencePatterns, setForbiddenSentencePatterns] = useState(DEFAULT_FORBIDDEN_SENTENCE_PATTERNS)
  const [rhetoricGuidelines, setRhetoricGuidelines] = useState(DEFAULT_RHETORIC_GUIDELINES)
  const [shortSentences, setShortSentences] = useState(false)
  const [customStylePrompt, setCustomStylePrompt] = useState('')
  const [styleSaving, setStyleSaving] = useState(false)
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const [showScrollBottom, setShowScrollBottom] = useState(false)
  const [matchedSkills, setMatchedSkills] = useState<SkillMatch[]>([])
  const abortRef = useRef<AbortController | null>(null)
  const [memoryModalOpen, setMemoryModalOpen] = useState(false)

  const handleMessagesScroll = useCallback(() => {
    const el = messagesRef.current
    if (!el) return
    const threshold = el.scrollHeight - el.clientHeight - 60
    setShowScrollBottom(el.scrollTop < threshold)
  }, [])

  const scrollToBottom = () => {
    messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight, behavior: 'smooth' })
  }

  const conversationScope = 'project'
  const scopeLabel = SCOPE_LABEL

  const fetchProjectStyle = useCallback(async () => {
    try {
      const res = await apiClient.get<ApiResponse<{
        narrative_perspective: string
        writing_style: string
        forbidden_sentence_patterns?: string | null
        rhetoric_guidelines?: string | null
        short_sentences?: boolean
        custom_style_prompt?: string | null
      }>>(`/projects/${projectId}`)
      setNarrativePerspective(res.data.data.narrative_perspective || 'third_person')
      setWritingStyle(res.data.data.writing_style || 'natural')
      setForbiddenSentencePatterns(mergeForbiddenPatterns(res.data.data.forbidden_sentence_patterns || ''))
      setRhetoricGuidelines(res.data.data.rhetoric_guidelines || DEFAULT_RHETORIC_GUIDELINES)
      setShortSentences(res.data.data.short_sentences || false)
      setCustomStylePrompt(res.data.data.custom_style_prompt || '')
    } catch {
      // Keep local defaults.
    }
  }, [projectId])

  const saveProjectStyle = async (
    nextPerspective = narrativePerspective,
    nextStyle = writingStyle,
    nextForbidden = forbiddenSentencePatterns,
    nextRhetoric = rhetoricGuidelines,
    nextShortSentences = shortSentences,
    nextCustomStylePrompt = customStylePrompt,
  ) => {
    setStyleSaving(true)
    try {
      await apiClient.put(`/projects/${projectId}`, {
        narrative_perspective: nextPerspective,
        writing_style: nextStyle,
        forbidden_sentence_patterns: stripDefaults(nextForbidden),
        rhetoric_guidelines: nextRhetoric,
        short_sentences: nextShortSentences,
        custom_style_prompt: nextCustomStylePrompt,
      })
      message.success('写作风格已保存')
    } catch (err: any) {
      message.error(err.message || '保存写作风格失败')
    } finally {
      setStyleSaving(false)
    }
  }

  const addRunLog = (log: Omit<WorkspaceRunLog, 'key'>) => {
    setRunLogs((prev) => [
      ...prev.slice(-19),
      { ...log, key: `${Date.now()}-${Math.random().toString(36).slice(2)}` },
    ])
  }

  const upsertConversation = (conversation?: WorkspaceAssistantConversation | null) => {
    if (!conversation) return
    setConversations((prev) => {
      const next = prev.some((item) => item.id === conversation.id)
        ? prev.map((item) => item.id === conversation.id ? conversation : item)
        : [conversation, ...prev]
      return next.sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))
    })
  }

  const refreshRunLogs = useCallback(async (runId: string) => {
    const res = await apiClient.get<ApiResponse<WorkspaceAssistantRunDetail>>(
      `/projects/${projectId}/ai/assistant/runs/${runId}`,
    )
    const detail = res.data.data
    setCurrentRun(detail.run || null)
    setRunLogs((detail.steps || []).map(runStepToLog))
    return detail
  }, [projectId])

  const updateLatestAssistant = (updater: (message: WorkspaceAssistantMessage) => WorkspaceAssistantMessage) => {
    setMessages((prev) => {
      const next = [...prev]
      const index = [...next].reverse().findIndex((item) => item.role === 'assistant')
      if (index < 0) return prev
      const realIndex = next.length - 1 - index
      next[realIndex] = updater(next[realIndex])
      return next
    })
  }

  const appendToolLog = (log: WorkspaceToolLog, content?: string) => {
    updateLatestAssistant((item) => {
      const data = item.data || createEmptyWorkspaceResponse()
      const shouldExposeAction =
        log.status === 'ok'
        && !!log.data
        && (log.tool === 'chapter_writer' || log.tool === 'preview_writing_context')
      return {
        ...item,
        content: content || item.content,
        data: {
          ...data,
          tool_logs: [...(data.tool_logs || []), log],
          applied_actions: shouldExposeAction
            ? [
                ...(data.applied_actions || []),
                { tool: log.tool, status: log.status, detail: log.detail, data: log.data },
              ]
            : data.applied_actions,
        },
      }
    })
  }

  const fetchConversations = useCallback(async () => {
    try {
      const res = await apiClient.get<ApiResponse<{ items: WorkspaceAssistantConversation[]; total: number }>>(
        `/projects/${projectId}/ai/assistant/conversations`,
        { scope: conversationScope },
      )
      const items = res.data.data.items || []
      setConversations(items)
      return items
    } catch {
      return []
    }
  }, [projectId])

  const loadConversation = useCallback(async (conversationId: string) => {
    setHistoryLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<{ conversation: WorkspaceAssistantConversation; messages: WorkspacePersistedMessage[] }>>(
        `/projects/${projectId}/ai/assistant/conversations/${conversationId}`,
      )
      setActiveConversationId(res.data.data.conversation.id)
      // The backend already returns persisted messages in conversation order.
      // Re-sorting here can scramble older rows that share the same timestamp.
      const loadedMessages = (res.data.data.messages || []).map(toWorkspaceMessage)
      setMessages(loadedMessages)
      const lastRun = [...loadedMessages].reverse().find((item) => item.role === 'assistant' && item.data?.run)?.data?.run || null
      upsertConversation(res.data.data.conversation)
      setInput('')
      setRunLogs([])
      setShowAllRunLogs(false)
      if (lastRun) {
        await refreshRunLogs(lastRun.id)
      } else {
        setCurrentRun(null)
      }
    } catch (err: any) {
      message.error(err.message || '加载对话失败')
    } finally {
      setHistoryLoading(false)
    }
  }, [projectId, refreshRunLogs])

  useEffect(() => {
    let mounted = true
    fetchProjectStyle()
    fetchConversations().then((items) => {
      if (mounted && items[0]) {
        loadConversation(items[0].id)
      }
    })
    return () => {
      mounted = false
      // Don't abort on unmount — backend will finish processing and commit results
    }
  }, [fetchConversations, fetchProjectStyle, loadConversation])

  const startNewConversation = () => {
    abortRef.current?.abort()
    setGenerating(false)
    setActiveConversationId(null)
    setMessages([])
    setInput('')
    setRunLogs([])
    setCurrentRun(null)
    setCurrentPlan(null)
    setShowAllRunLogs(false)
  }

  const deleteConversation = (conversationId: string) => {
    Modal.confirm({
      title: '删除对话',
      content: '确定要删除这条对话记录吗？删除后无法恢复。',
      okText: '删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await apiClient.delete(`/projects/${projectId}/ai/assistant/conversations/${conversationId}`)
          setConversations((prev) => prev.filter((item) => item.id !== conversationId))
          if (activeConversationId === conversationId) {
            setActiveConversationId(null)
            setMessages([])
          }
          message.success('对话已删除')
        } catch (err: any) {
          message.error(err.message || '删除对话失败')
        }
      },
    })
  }

  const stopGeneration = () => {
    abortRef.current?.abort()
    setGenerating(false)
    addRunLog({ tool: scope, status: 'skipped', message: '已停止当前AI任务' })
    updateLatestAssistant((item) => ({
      ...item,
      content: item.content ? item.content + '\n\n（已停止生成）' : '已停止生成。',
      status: 'aborted',
    }))
  }

  const retryStep = async (stepId: string, tool: string) => {
    if (!currentRun) return
    setRetryingStepId(stepId)
    try {
      const res = await apiClient.post<ApiResponse<{
        id: string
        status: string
        tool?: string
        detail?: string
        attempt_no?: number
        retry_of_step_id?: string
        resolved_step_id?: string
      }>>(
        `/projects/${projectId}/ai/assistant/runs/${currentRun.id}/steps/${stepId}/retry`,
      )
      const data = res.data.data
      await refreshRunLogs(currentRun.id)
      // Add the new retry step to run logs
      addRunLog({
        tool: tool,
        status: data.status || 'ok',
        message: `重试 #${data.attempt_no || 1}: ${data.detail || (data.status === 'error' ? '重试失败' : '重试成功')}`,
        stepId: data.id,
        attemptNo: data.attempt_no || 1,
        retryOfStepId: data.retry_of_step_id,
      })
      // Mark original step as resolved in run logs
      if (data.status !== 'error') {
        setRunLogs((prev) =>
          prev.map((log) =>
            log.stepId === stepId
              ? { ...log, resolvedStepId: data.id }
              : log
          )
        )
      }
      if (data.status === 'error') {
        message.error(data.detail || `「${tool}」重试失败`)
      } else {
        message.success(`「${tool}」重试成功`)
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || err.message || '重试失败')
    } finally {
      setRetryingStepId(null)
    }
  }

  const resumeFromStep = async (stepId: string, tool: string) => {
    if (!currentRun) return
    setRetryingStepId(stepId)
    try {
      const res = await apiClient.post<ApiResponse<Array<{
        id: string
        status: string
        tool?: string
        detail?: string
        attempt_no?: number
        retry_of_step_id?: string
      }>>>(
        `/projects/${projectId}/ai/assistant/runs/${currentRun.id}/steps/${stepId}/resume-from`,
      )
      const results = res.data.data
      await refreshRunLogs(currentRun.id)
      for (const r of results) {
        addRunLog({
          tool: r.tool || tool,
          status: r.status || 'ok',
          message: `重试 #${r.attempt_no || 1}: ${r.detail || (r.status === 'error' ? '失败' : '成功')}`,
          stepId: r.id,
          attemptNo: r.attempt_no || 1,
          retryOfStepId: r.retry_of_step_id,
        })
      }
      const failed = results.filter((r) => r.status === 'error')
      if (failed.length > 0) {
        message.warning(`从该步骤继续完成，${failed.length} 个步骤仍然失败`)
      } else {
        message.success('从该步骤继续执行完成')
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || err.message || '继续执行失败')
    } finally {
      setRetryingStepId(null)
    }
  }

  const resumeRun = async () => {
    if (!currentRun) return
    setRetryingStepId('all')
    try {
      const res = await apiClient.post<ApiResponse<Array<{
        id: string
        status: string
        tool?: string
        detail?: string
        attempt_no?: number
      }>>>(
        `/projects/${projectId}/ai/assistant/runs/${currentRun.id}/resume`,
      )
      const results = res.data.data
      await refreshRunLogs(currentRun.id)
      for (const r of results) {
        addRunLog({
          tool: r.tool || 'step',
          status: r.status || 'ok',
          message: `重试 #${r.attempt_no || 1}: ${r.detail || (r.status === 'error' ? '失败' : '成功')}`,
          stepId: r.id,
          attemptNo: r.attempt_no || 1,
        })
      }
      const failed = results.filter((r) => r.status === 'error')
      if (failed.length > 0) {
        message.warning(`重试完成，${failed.length} 个步骤仍然失败`)
      } else {
        message.success('所有失败步骤已重试成功')
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || err.message || '重试失败')
    } finally {
      setRetryingStepId(null)
    }
  }

  const retryPlanStep = async (stepKey: string) => {
    if (!currentPlan) return
    setRetryingPlanKey(stepKey)
    try {
      const res = await apiClient.post<ApiResponse<Record<string, unknown>>>(
        `/projects/${projectId}/ai/agent/plans/${currentPlan.plan_id}/steps/${stepKey}/retry`,
      )
      const data = res.data.data
      setCurrentPlan((prev) => {
        if (!prev) return null
        return {
          ...prev,
          steps: prev.steps.map((s) =>
            s.step_key === stepKey
              ? {
                  ...s,
                  status: (data.status as AgentPlanStepView['status']) || 'ok',
                  detail: data.detail as string | undefined,
                  error: data.error as string | undefined,
                  attempt_no: (data.attempt_no as number) || 1,
                }
              : s
          ),
        }
      })
      addRunLog({
        tool: (data.tool as string) || stepKey,
        status: (data.status as string) || 'ok',
        message: `重试: ${data.detail || (data.status === 'error' ? '失败' : '成功')}`,
      })
      if (data.status === 'error') {
        message.error((data.detail as string) || `步骤「${stepKey}」重试失败`)
      } else {
        message.success(`步骤「${stepKey}」重试成功`)
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || err.message || '重试失败')
    } finally {
      setRetryingPlanKey(null)
    }
  }

  const handlePlanSseFrame = (frame: string) => {
    const data = frame
      .split(/\r?\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.replace(/^data:\s?/, ''))
      .join('\n')
    if (!data || data === '[DONE]') return
    const event = JSON.parse(data)
    if (event.type === 'step_start') {
      const ev = event as { step_key: string; tool: string }
      setCurrentPlan((prev) => {
        if (!prev) return null
        return {
          ...prev,
          steps: prev.steps.map((s) =>
            s.step_key === ev.step_key ? { ...s, status: 'running' as const } : s
          ),
        }
      })
    } else if (event.type === 'step_result') {
      const ev = event as {
        step_key: string
        tool: string
        status: string
        detail?: string
        error?: string
        data?: Record<string, unknown>
      }
      setCurrentPlan((prev) => {
        if (!prev) return null
        return {
          ...prev,
          steps: prev.steps.map((s) =>
            s.step_key === ev.step_key
              ? { ...s, status: (ev.status as AgentPlanStepView['status']) || 'ok', detail: ev.detail, error: ev.error }
              : s
          ),
        }
      })
      addRunLog({ tool: ev.tool, status: ev.status, message: ev.detail || ev.tool })
      appendToolLog({ tool: ev.tool, status: ev.status, detail: ev.detail, data: ev.data })
    } else if (event.type === 'step_skip') {
      const ev = event as { step_key: string }
      setCurrentPlan((prev) => {
        if (!prev) return null
        return {
          ...prev,
          steps: prev.steps.map((s) =>
            s.step_key === ev.step_key ? { ...s, status: 'skipped' as const } : s
          ),
        }
      })
    } else if (event.type === 'step_blocked') {
      const ev = event as { step_key: string; detail?: string }
      setCurrentPlan((prev) => {
        if (!prev) return null
        return {
          ...prev,
          steps: prev.steps.map((s) =>
            s.step_key === ev.step_key ? { ...s, status: 'blocked' as const, detail: ev.detail } : s
          ),
        }
      })
    } else if (event.type === 'plan_end') {
      const ev = event as { status: string; error?: string }
      setCurrentPlan((prev) => prev ? { ...prev, status: (ev.status as AgentPlanViewState['status']) || 'completed' } : null)
      addRunLog({
        tool: 'plan',
        status: ev.status === 'completed' ? 'ok' : 'error',
        message: ev.status === 'completed' ? '计划执行完成' : (ev.error || '计划执行失败'),
      })
    }
  }

  const streamPlanRequest = async (url: string) => {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
    if (!res.ok || !res.body) {
      const errText = await res.text().catch(() => '')
      throw new Error(errText || `HTTP ${res.status}`)
    }
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
        if (frame.trim()) handlePlanSseFrame(frame)
      }
    }
    buffer += decoder.decode()
    if (buffer.trim()) handlePlanSseFrame(buffer)
  }

  const resumeFromPlanStep = async (stepKey: string) => {
    if (!currentPlan) return
    setRetryingPlanKey(stepKey)
    try {
      await streamPlanRequest(
        `/api/v1/projects/${projectId}/ai/agent/plans/${currentPlan.plan_id}/steps/${stepKey}/resume-from/stream`,
      )
      message.success('从该步骤继续执行完成')
    } catch (err: any) {
      message.error(err.message || '继续执行失败')
    } finally {
      setRetryingPlanKey(null)
    }
  }

  const retryAllPlanSteps = async () => {
    if (!currentPlan) return
    setRetryingPlanKey('__all__')
    try {
      await streamPlanRequest(
        `/api/v1/projects/${projectId}/ai/agent/plans/${currentPlan.plan_id}/resume/stream`,
      )
      message.success('重试完成')
    } catch (err: any) {
      message.error(err.message || '重试失败')
    } finally {
      setRetryingPlanKey(null)
    }
  }

  const showStepDetail = async (stepId: string) => {
    if (!currentRun) return
    try {
      const res = await apiClient.get<ApiResponse<{
        run: unknown
        steps: Array<{ id: string; tool?: string; request?: unknown; result?: unknown; error?: string; attempt_no?: number; status?: string }>
      }>>(`/projects/${projectId}/ai/assistant/runs/${currentRun.id}`)
      const step = (res.data.data.steps || []).find((s) => s.id === stepId)
      if (step) {
        setDetailStep(step)
      }
    } catch {
      message.error('获取步骤详情失败')
    }
  }

  const sendMessage = async () => {
    const userText = input.trim()
    if (!userText) {
      message.warning('请输入要发送给AI的内容')
      return
    }

    setGenerating(true)
    setRunLogs([{ key: `${Date.now()}-start`, tool: scope, status: 'running', message: '正在提交给AI助手' }])
    setCurrentRun(null)
    setCurrentPlan(null)
    setShowAllRunLogs(false)
    setMatchedSkills([])
    const controller = new AbortController()
    abortRef.current = controller
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: userText, status: 'completed' },
      {
        role: 'assistant',
        content: '正在分析需求...',
        status: 'running',
        data: createEmptyWorkspaceResponse([{ tool: scope, status: 'running', detail: 'AI 正在搜索和分析...' }]),
      },
    ])
    setInput('')

    try {
      const history = messages.slice(-8).map((item) => ({
        role: item.role,
        content: item.content,
      }))
      const res = await fetch(`/api/v1/projects/${projectId}/ai/workspace-assistant/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scope: conversationScope,
          outline_batch_count: outlineBatchCount,
          message: userText,
          conversation_id: activeConversationId || undefined,
          selected_outline_node_id: selectedOutlineNodeId || undefined,
          selected_character_id: selectedCharacterId || undefined,
          selected_text: selectedText || undefined,
          selected_text_chapter_id: selectedTextChapterId || undefined,
          model: model || defaultModel || undefined,
          assistant_mode: assistantMode,
          temperature,
          max_tokens: maxTokens || undefined,
          auto_apply: autoApply,
          history,
        }),
        signal: controller.signal,
      })
      if (!res.ok || !res.body) throw new Error('请求失败')

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let completed = false

      const handleFrame = (frame: string) => {
        const data = frame
          .split(/\r?\n/)
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.replace(/^data:\s?/, ''))
          .join('\n')
        if (!data || data === '[DONE]') return
        let event: any
        try {
          event = JSON.parse(data)
        } catch {
          return // skip malformed SSE frames
        }
        if (event.type === 'conversation') {
          const conversation = event.conversation as WorkspaceAssistantConversation
          const persistedUser = event.user_message as WorkspacePersistedMessage
          const persistedAssistant = event.assistant_message as WorkspacePersistedMessage
          setActiveConversationId(conversation.id)
          upsertConversation(conversation)
          setMessages((prev) => {
            const next = [...prev]
            const assistantIndex = [...next].reverse().findIndex((item) => item.role === 'assistant')
            const realAssistantIndex = assistantIndex >= 0 ? next.length - 1 - assistantIndex : -1
            if (realAssistantIndex >= 1 && !next[realAssistantIndex].id && !next[realAssistantIndex - 1].id) {
              next.splice(realAssistantIndex - 1, 2, toWorkspaceMessage(persistedUser), toWorkspaceMessage(persistedAssistant))
              return sortWorkspaceMessages(next)
            }
            return sortWorkspaceMessages([...prev, toWorkspaceMessage(persistedUser), toWorkspaceMessage(persistedAssistant)])
          })
        } else if (event.type === 'run') {
          const run = event.run as WorkspaceAssistantRun
          setCurrentRun(run)
          addRunLog({ tool: 'run', status: run.status || 'running', message: `任务已创建：${run.id.slice(0, 8)}` })
        } else if (event.type === 'status') {
          const detail = event.message || '正在执行'
          const log = { tool: event.tool || scope, status: 'running', detail, stepId: event.step_id }
          addRunLog({ tool: log.tool, status: log.status, message: detail, stepId: event.step_id })
          appendToolLog(log, `正在执行：${detail}`)
        } else if (event.type === 'tool') {
          const detail = event.detail || event.message || event.tool
          const log = { tool: event.tool || 'tool', status: event.status || 'ok', detail, stepId: event.step_id }
          if (log.tool !== 'planner') {
            addRunLog({ tool: log.tool, status: log.status, message: `${log.tool}: ${detail}`, stepId: event.step_id })
          }
          appendToolLog(log)
        } else if (event.type === 'iteration_start') {
          // silently track iteration progress
        } else if (event.type === 'iteration_end') {
          // silently track, don't show in run log
        } else if (event.type === 'search_start') {
          const ev = event as { tool: string; args?: Record<string, unknown>; iteration: number; step_id?: string }
          const argsStr = JSON.stringify(ev.args || {}).slice(0, 80)
          addRunLog({ tool: ev.tool, status: 'running', message: `正在搜索: ${argsStr}`, stepId: ev.step_id })
        } else if (event.type === 'search_result') {
          const ev = event as { tool: string; result?: { detail?: string; status?: string }; iteration: number; step_id?: string }
          const detail = ev.result?.detail || '搜索完成'
          const status = ev.result?.status || 'ok'
          addRunLog({ tool: ev.tool, status, message: detail, stepId: ev.step_id })
          appendToolLog({ tool: ev.tool, status, detail, stepId: ev.step_id })
        } else if (event.type === 'plan_created') {
          const ev = event as { plan_id: string; plan_name: string; steps: Array<{ step_key: string; tool: string; status: string; label?: string }> }
          setCurrentPlan({
            plan_id: ev.plan_id,
            plan_name: ev.plan_name,
            status: 'running',
            steps: ev.steps.map((s) => ({
              step_key: s.step_key,
              tool: s.tool,
              status: (s.status as AgentPlanStepView['status']) || 'pending',
              label: s.label,
            })),
          })
          addRunLog({ tool: 'plan', status: 'running', message: `计划「${ev.plan_name}」已创建，${ev.steps.length} 个步骤` })
        } else if (event.type === 'plan_start') {
          setCurrentPlan((prev) => prev ? { ...prev, status: 'running' } : null)
        } else if (event.type === 'step_start') {
          const ev = event as { step_key: string; tool: string; attempt_no?: number }
          setCurrentPlan((prev) => {
            if (!prev) return null
            return {
              ...prev,
              steps: prev.steps.map((s) =>
                s.step_key === ev.step_key
                  ? { ...s, status: 'running' as const, attempt_no: ev.attempt_no }
                  : s
              ),
            }
          })
          addRunLog({ tool: ev.tool, status: 'running', message: `执行: ${ev.tool}` })
        } else if (event.type === 'step_result') {
          const ev = event as { step_key: string; tool: string; status: string; detail?: string; error?: string; data?: Record<string, unknown> }
          setCurrentPlan((prev) => {
            if (!prev) return null
            return {
              ...prev,
              steps: prev.steps.map((s) =>
                s.step_key === ev.step_key
                  ? { ...s, status: (ev.status as AgentPlanStepView['status']) || 'ok', detail: ev.detail, error: ev.error }
                  : s
              ),
            }
          })
          addRunLog({ tool: ev.tool, status: ev.status, message: ev.detail || (ev.status === 'ok' ? '完成' : '失败') })
          appendToolLog({ tool: ev.tool, status: ev.status, detail: ev.detail, data: ev.data })
        } else if (event.type === 'step_skip') {
          const ev = event as { step_key: string; tool: string; status: string }
          setCurrentPlan((prev) => {
            if (!prev) return null
            return {
              ...prev,
              steps: prev.steps.map((s) =>
                s.step_key === ev.step_key ? { ...s, status: 'skipped' as const } : s
              ),
            }
          })
        } else if (event.type === 'step_blocked') {
          const ev = event as { step_key: string; tool: string; detail?: string }
          setCurrentPlan((prev) => {
            if (!prev) return null
            return {
              ...prev,
              steps: prev.steps.map((s) =>
                s.step_key === ev.step_key
                  ? { ...s, status: 'blocked' as const, detail: ev.detail }
                  : s
              ),
            }
          })
        } else if (event.type === 'plan_end') {
          const ev = event as { plan_id: string; status: string; error?: string }
          setCurrentPlan((prev) => {
            if (!prev) return null
            return { ...prev, status: (ev.status as AgentPlanViewState['status']) || 'completed' }
          })
          addRunLog({
            tool: 'plan',
            status: ev.status === 'completed' ? 'ok' : 'error',
            message: ev.status === 'completed' ? '计划执行完成' : (ev.error || '计划执行失败'),
          })
        } else if (event.type === 'no_plan') {
          // No plan detected, fell back to old flow — handled by subsequent events
        } else if (event.type === 'skills_matched') {
          const ev = event as { skills: Array<{ name: string; description?: string; truncated?: boolean; warnings?: string[]; recommended_tools?: string[]; injected?: boolean }> }
          setMatchedSkills(ev.skills || [])
        } else if (event.type === 'thinking_delta') {
          const ev = event as { delta: string }
          setMessages((prev) => {
            const next = [...prev]
            const aiIdx = [...next].reverse().findIndex((item) => item.role === 'assistant')
            if (aiIdx < 0) return prev
            const realIdx = next.length - 1 - aiIdx
            next[realIdx] = {
              ...next[realIdx],
              content: next[realIdx].content === '正在分析需求...'
                ? ev.delta
                : next[realIdx].content + ev.delta,
            }
            return next
          })
        } else if (event.type === 'thinking') {
          const ev = event as { content: string; iteration: number }
          setMessages((prev) => {
            const next = [...prev]
            const aiIdx = [...next].reverse().findIndex((item) => item.role === 'assistant')
            if (aiIdx < 0) return prev
            const realIdx = next.length - 1 - aiIdx
            // Replace raw streaming JSON with clean parsed reply
            next[realIdx] = { ...next[realIdx], content: ev.content }
            return next
          })
        } else if (event.type === 'complete') {
          const payload = event.data as WorkspaceAssistantResponse
          const reply = payload.reply?.trim() || EMPTY_ASSISTANT_REPLY
          completed = true
          if (payload.run) setCurrentRun(payload.run)
          upsertConversation(payload.conversation)
          setMessages((prev) => {
            const next = [...prev]
            const index = [...next].reverse().findIndex((item) => item.role === 'assistant')
            if (index < 0) return [...prev, { role: 'assistant', content: reply, data: payload }]
            const realIndex = next.length - 1 - index
            next[realIndex] = {
              id: payload.message?.id || next[realIndex].id,
              conversation_id: payload.message?.conversation_id || next[realIndex].conversation_id,
              role: 'assistant',
              content: reply,
              status: payload.message?.status || 'completed',
              created_at: payload.message?.created_at || next[realIndex].created_at,
              updated_at: payload.message?.updated_at || next[realIndex].updated_at,
              data: payload,
            }
            return next
          })
          addRunLog({ tool: scope, status: 'ok', message: 'AI助手已完成' })
          fetchConversations()
          Promise.resolve(onApplied?.()).catch(() => undefined)
        } else if (event.type === 'error') {
          throw new Error(event.message || 'AI助手执行失败')
        }
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const frames = buffer.split(/\r?\n\r?\n/)
        buffer = frames.pop() || ''
        for (const frame of frames) {
          if (frame.trim()) handleFrame(frame)
        }
      }
      buffer += decoder.decode()
      if (buffer.trim()) handleFrame(buffer)
      if (!completed && !controller.signal.aborted) {
        // Treat incomplete stream as partial result rather than hard error
        updateLatestAssistant((item) => ({
          ...item,
          status: 'done',
        }))
        addRunLog({ tool: scope, status: 'success', message: 'AI响应已结束（部分内容）' })
      }
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        message.error(err.message || 'AI助手执行失败')
        addRunLog({ tool: scope, status: 'error', message: err.message || 'AI助手执行失败' })
        updateLatestAssistant((item) => ({ ...item, content: err.message || 'AI助手执行失败', status: 'error' }))
      }
    } finally {
      setGenerating(false)
      abortRef.current = null
    }
  }

  return (
    <section className="workspace-assistant-chat" data-testid={`${scope}-ai-chat`}>
      <div className="workspace-assistant-head">
        <Text strong>{scopeLabel}</Text>
        <Space size={4}>
          <Popover
            trigger="click"
            title="助手设置"
            content={(
              <div style={{ width: 260 }}>
                {/* ── AI 运行设置 ── */}
                <Text type="secondary" style={{ display: 'block', fontSize: 11, fontWeight: 600, letterSpacing: '0.05em', marginBottom: 8, textTransform: 'uppercase' }}>
                  模型与运行
                </Text>
                <Space direction="vertical" size={8} style={{ width: '100%', marginBottom: 12 }}>
                  <Select
                    allowClear
                    showSearch
                    size="small"
                    value={selectedModelValue}
                    onChange={handleModelChange}
                    options={modelSelectOptions}
                    loading={modelsLoading}
                    optionFilterProp="label"
                    placeholder={modelOptions.length ? '选择AI模型' : '请先在系统设置配置模型'}
                    notFoundContent={modelsLoading ? '加载模型中...' : '暂无已配置模型'}
                  />
                  <div>
                    <Text type="secondary" style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>助手模式</Text>
                    <Select
                      size="small"
                      value={assistantMode}
                      onChange={(value) => setAssistantMode(value)}
                      style={{ width: '100%' }}
                      options={[
                        { value: 'fast', label: '快速（默认）' },
                        { value: 'quality', label: '质量' },
                      ]}
                    />
                  </div>
                  <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Text type="secondary">
                      自动执行工具 <Tooltip title="开启后 AI 调用工具时无需逐次确认，关闭则每次都需要你批准"><InfoCircleOutlined style={{ fontSize: 11, opacity: 0.5 }} /></Tooltip>
                    </Text>
                    <Switch size="small" checked={autoApply} onChange={setAutoApply} />
                  </Space>
                  <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Text type="secondary">
                      连续规划章数 <Tooltip title="AI 一次性连续规划的章节数量，数值越大规划越连贯但耗时更长"><InfoCircleOutlined style={{ fontSize: 11, opacity: 0.5 }} /></Tooltip>
                    </Text>
                    <InputNumber
                      size="small"
                      min={1}
                      max={12}
                      value={outlineBatchCount}
                      onChange={(value) => setOutlineBatchCount(Number(value || 1))}
                      style={{ width: 92 }}
                    />
                  </Space>
                  <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Text type="secondary">
                      温度 <Tooltip title="控制输出随机性。0 最确定，2 最随机。推荐 0.3-0.7"><InfoCircleOutlined style={{ fontSize: 11, opacity: 0.5 }} /></Tooltip>
                    </Text>
                    <InputNumber
                      size="small"
                      min={0}
                      max={2}
                      step={0.1}
                      value={temperature}
                      onChange={(value) => setTemperature(Number(value ?? 0.3))}
                      style={{ width: 92 }}
                    />
                  </Space>
                  <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Text type="secondary">
                      最大输出 <Tooltip title="单次 AI 回复的最大 token 数。留空使用模型默认值"><InfoCircleOutlined style={{ fontSize: 11, opacity: 0.5 }} /></Tooltip>
                    </Text>
                    <InputNumber
                      size="small"
                      min={256}
                      step={256}
                      value={maxTokens ?? undefined}
                      placeholder="默认"
                      onChange={(value) => setMaxTokens(value ? Number(value) : null)}
                      style={{ width: 112 }}
                    />
                  </Space>
                </Space>

                <div style={{ height: 1, background: 'var(--ant-color-border-secondary)', margin: '4px 0 12px' }} />

                {/* ── 写作风格 ── */}
                <Text type="secondary" style={{ display: 'block', fontSize: 11, fontWeight: 600, letterSpacing: '0.05em', marginBottom: 8, textTransform: 'uppercase' }}>
                  写作风格
                </Text>
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <div>
                    <Text type="secondary" style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>叙事视角</Text>
                    <Select
                      size="small"
                      value={narrativePerspective}
                      onChange={(value) => {
                        setNarrativePerspective(value)
                        saveProjectStyle(value, writingStyle)
                      }}
                      loading={styleSaving}
                      style={{ width: '100%' }}
                      options={[
                        { value: 'third_person', label: '第三人称' },
                        { value: 'first_person', label: '第一人称' },
                        { value: 'omniscient', label: '上帝视角' },
                      ]}
                    />
                  </div>
                  <div>
                    <Text type="secondary" style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>文风偏好</Text>
                    <Select
                      size="small"
                      value={writingStyle}
                      onChange={(value) => {
                        setWritingStyle(value)
                        saveProjectStyle(narrativePerspective, value)
                      }}
                      loading={styleSaving}
                      style={{ width: '100%' }}
                      options={[
                        { value: 'natural', label: '自然' },
                        { value: 'vivid', label: '华丽生动' },
                        { value: 'concise', label: '白描简洁' },
                        { value: 'serious', label: '严肃' },
                        { value: 'humorous', label: '幽默' },
                        { value: 'poetic', label: '诗意' },
                      ]}
                    />
                  </div>
                  <div>
                    <Text type="secondary" style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>禁用句式</Text>
                    <TextArea
                      size="small"
                      value={forbiddenSentencePatterns}
                      onChange={(event) => setForbiddenSentencePatterns(event.target.value)}
                      onBlur={(event) => saveProjectStyle(narrativePerspective, writingStyle, event.target.value, rhetoricGuidelines)}
                      autoSize={{ minRows: 3, maxRows: 5 }}
                      disabled={styleSaving}
                    />
                  </div>
                  <div>
                    <Text type="secondary" style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>修辞限制</Text>
                    <TextArea
                      size="small"
                      value={rhetoricGuidelines}
                      onChange={(event) => setRhetoricGuidelines(event.target.value)}
                      onBlur={(event) => saveProjectStyle(narrativePerspective, writingStyle, forbiddenSentencePatterns, event.target.value)}
                      autoSize={{ minRows: 3, maxRows: 5 }}
                      disabled={styleSaving}
                    />
                  </div>
                  <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Text type="secondary">短句模式</Text>
                    <Switch
                      size="small"
                      checked={shortSentences}
                      onChange={(checked) => {
                        setShortSentences(checked)
                        saveProjectStyle(narrativePerspective, writingStyle, forbiddenSentencePatterns, rhetoricGuidelines, checked)
                      }}
                    />
                  </Space>
                  <div>
                    <Text type="secondary" style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>自定义风格提示词</Text>
                    <TextArea
                      size="small"
                      value={customStylePrompt}
                      onChange={(event) => setCustomStylePrompt(event.target.value)}
                      onBlur={(event) => saveProjectStyle(narrativePerspective, writingStyle, forbiddenSentencePatterns, rhetoricGuidelines, shortSentences, event.target.value)}
                      autoSize={{ minRows: 2, maxRows: 6 }}
                      disabled={styleSaving}
                      placeholder="追加到所有AI文案生成中的自定义指令"
                    />
                  </div>
                </Space>
                <Button
                  size="small"
                  onClick={() => {
                    setForbiddenSentencePatterns(DEFAULT_FORBIDDEN_SENTENCE_PATTERNS)
                    setRhetoricGuidelines(DEFAULT_RHETORIC_GUIDELINES)
                    saveProjectStyle(narrativePerspective, writingStyle, DEFAULT_FORBIDDEN_SENTENCE_PATTERNS, DEFAULT_RHETORIC_GUIDELINES, shortSentences, customStylePrompt)
                  }}
                >
                  恢复默认表达限制
                </Button>
              </div>
            )}
          >
            <Button size="small" icon={<SettingOutlined />} />
          </Popover>
          <Button size="small" onClick={() => setMemoryModalOpen(true)}>记忆</Button>
          <Button size="small" icon={<ReloadOutlined />} loading={historyLoading} onClick={fetchConversations} />
          <Button size="small" type="primary" icon={<PlusOutlined />} onClick={startNewConversation}>新对话</Button>
        </Space>
      </div>

      <div className="workspace-assistant-history">
        {conversations.length > 0 ? conversations.map((conversation) => (
          <button
            type="button"
            key={conversation.id}
            className={`workspace-assistant-history-item${conversation.id === activeConversationId ? ' workspace-assistant-history-item-active' : ''}`}
            onClick={() => loadConversation(conversation.id)}
          >
            <span title={conversation.title}>{conversation.title}</span>
            <Button
              type="text"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={(event) => {
                event.stopPropagation()
                deleteConversation(conversation.id)
              }}
            />
          </button>
        )) : (
          <Text type="secondary" style={{ fontSize: 12 }}>还没有历史对话。</Text>
        )}
      </div>

      {currentPlan && (
        <div style={{ padding: '0 12px' }}>
          <AgentPlanView
            plan={currentPlan}
            onRetryStep={retryPlanStep}
            onResumeFromStep={resumeFromPlanStep}
            onRetryAll={retryAllPlanSteps}
            retryingKey={retryingPlanKey}
          />
        </div>
      )}

      {runLogs.length > 0 && !currentPlan && (
        <div className="workspace-assistant-run-log">
          <div className="workspace-assistant-run-log-header">
            <Space size={6}>
              <Text type="secondary" style={{ fontSize: 12 }}>运行过程</Text>
              {currentRun && (
                <Tag color={currentRun.status === 'completed' ? 'green' : currentRun.status === 'error' ? 'red' : 'blue'}>
                  {currentRun.status} #{currentRun.id.slice(0, 8)}
                </Tag>
              )}
              {runLogs.some((l) => l.status === 'error' && !l.resolvedStepId) && (
                <Button
                  size="small"
                  loading={retryingStepId === 'all'}
                  disabled={retryingStepId !== null}
                  onClick={resumeRun}
                >
                  重试全部失败步骤
                </Button>
              )}
            </Space>
            {runLogs.length > 3 && (
              <button
                type="button"
                className="workspace-assistant-run-log-toggle"
                onClick={() => setShowAllRunLogs((prev) => !prev)}
              >
                {showAllRunLogs ? `收起（仅显示最新3条）` : `展开全部（${runLogs.length}条）`}
              </button>
            )}
          </div>
          {(showAllRunLogs ? runLogs : runLogs.slice(-3)).map((log) => (
            <div className="workspace-assistant-run-log-item" key={log.key}>
              <Tag color={
                log.status === 'ok' && log.resolvedStepId ? 'blue' :
                log.status === 'ok' ? 'green' :
                log.status === 'error' && log.resolvedStepId ? 'green' :
                log.status === 'error' ? 'red' :
                log.status === 'skipped' ? 'orange' : 'blue'
              }>
                {log.status === 'error' && log.resolvedStepId ? '已解决' :
                 log.attemptNo && log.attemptNo > 1 ? `重试 #${log.attemptNo}` :
                 log.status || 'running'}
              </Tag>
              {log.tool && <Text code>{log.tool}</Text>}
              <Text>{log.message}</Text>
              {log.status === 'error' && !log.resolvedStepId && log.stepId && currentRun && (
                <Space size={4}>
                  <Button
                    type="link"
                    size="small"
                    icon={<ReloadOutlined />}
                    loading={retryingStepId === log.stepId}
                    disabled={retryingStepId !== null}
                    onClick={() => retryStep(log.stepId!, log.tool || 'tool')}
                    title="仅重试此步骤"
                  >重试</Button>
                  <Button
                    type="link"
                    size="small"
                    loading={retryingStepId === log.stepId}
                    disabled={retryingStepId !== null}
                    onClick={() => resumeFromStep(log.stepId!, log.tool || 'tool')}
                    title="重试此步骤并继续后续步骤"
                  >从这里继续</Button>
                  <Button
                    type="link"
                    size="small"
                    icon={<InfoCircleOutlined />}
                    onClick={() => showStepDetail(log.stepId!)}
                    title="查看详情"
                  >详情</Button>
                </Space>
              )}
            </div>
          ))}
        </div>
      )}

      <MessageList
        messages={messages}
        generating={generating}
        matchedSkills={matchedSkills}
        showScrollBottom={showScrollBottom}
        onScrollToBottom={scrollToBottom}
        messagesRef={messagesRef}
        onScroll={handleMessagesScroll}
      />

      <Composer
        input={input}
        generating={generating}
        selectedText={selectedText}
        showSelectionTag={showSelectionTag}
        messageCount={messages.length}
        onInputChange={setInput}
        onSend={sendMessage}
        onStop={stopGeneration}
        onCloseSelectionTag={() => setShowSelectionTag(false)}
      />

      <StepDetailModal
        detail={detailStep}
        onClose={() => setDetailStep(null)}
      />
      <AssistantMemoryModal
        projectId={projectId}
        open={memoryModalOpen}
        onClose={() => setMemoryModalOpen(false)}
      />
    </section>
  )
}

export default WorkspaceAssistantChat
