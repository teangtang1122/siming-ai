import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Modal, Select, Space, Tag, Tooltip, Typography, message } from 'antd'
import {
  DeleteOutlined,
  InfoCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { AssistantMemoryModal } from './AssistantMemoryModal'
import {
  type ApiResponse,
  type WorkspaceAssistantChatProps,
  type WorkspaceAssistantConversation,
  type WorkspaceAssistantMessage,
  type WorkspaceAssistantResponse,
  type WorkspaceAssistantRun,
  type WorkspaceAssistantRunDetail,
  type WorkspaceAssistantRunStatus,
  type WorkspacePersistedMessage,
  type WorkspaceRunLog,
  type WorkspaceToolLog,
  type StepDetail,
  SCOPE_LABEL,
  assistantOutcomeToRunLog,
  createEmptyWorkspaceResponse,
  runStepToLog,
  sortWorkspaceMessages,
  toWorkspaceMessage,
  MessageList,
  Composer,
  StepDetailModal,
} from './assistant'
import { motionAwareScrollBehavior } from '../utils/motion'
import { extractExplicitLocalPaths } from '../utils/localCliPathGrant'
import { useOperations } from '../shared/operations/queries'
import { projectCatalogingMessages } from './assistant/catalogingNotifications'
import { useAiPanelContext, type GeneratedChapterDraft } from '../contexts/AiPanelContext'
import './WorkspaceAssistantChat.css'

const { Text } = Typography
const EMPTY_ASSISTANT_REPLY = '没有收到模型的文字回复。请重试一次，或在系统设置里测试当前模型/CLI 是否支持项目助手的流式输出和工具调用。'

const TERMINAL_RUN_STATUSES = new Set<WorkspaceAssistantRunStatus>([
  'completed',
  'error',
  'aborted',
  'cancelled',
  'interrupted',
])
const RUN_STATUS_LABELS: Record<WorkspaceAssistantRunStatus, string> = {
  queued: '排队中',
  running: '执行中',
  completed: '已完成',
  error: '失败',
  aborted: '已中止',
  cancelled: '已取消',
  interrupted: '已中断',
}
const MAX_RUN_POLL_FAILURES = 5

interface ActiveAssistantExecution {
  token: number
  controller: AbortController
  userMessageId: string
  assistantMessageId: string
  conversationId: string | null
  run: WorkspaceAssistantRun | null
  terminalHandled: boolean
}

class AssistantRunRequestError extends Error {
  status?: number

  constructor(message: string, status?: number) {
    super(message)
    this.name = 'AssistantRunRequestError'
    this.status = status
  }
}

const isAbortError = (error: unknown) => (
  error instanceof DOMException
    ? error.name === 'AbortError'
    : Boolean(error && typeof error === 'object' && 'name' in error && error.name === 'AbortError')
)

const abortableDelay = (milliseconds: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => {
  if (signal.aborted) {
    reject(new DOMException('请求已取消', 'AbortError'))
    return
  }
  const timer = window.setTimeout(() => {
    signal.removeEventListener('abort', onAbort)
    resolve()
  }, milliseconds)
  const onAbort = () => {
    window.clearTimeout(timer)
    reject(new DOMException('请求已取消', 'AbortError'))
  }
  signal.addEventListener('abort', onAbort, { once: true })
})

async function fetchAssistantRunDetail(
  projectId: string,
  runId: string,
  signal: AbortSignal,
): Promise<WorkspaceAssistantRunDetail> {
  const response = await fetch(`/api/v1/projects/${projectId}/ai/assistant/runs/${runId}`, { signal })
  if (!response.ok) {
    let detail = `恢复任务状态失败（${response.status}）`
    try {
      const payload = await response.json() as { detail?: string; message?: string }
      detail = payload.detail || payload.message || detail
    } catch {
      // Keep the status-based message when the server did not return JSON.
    }
    throw new AssistantRunRequestError(detail, response.status)
  }
  const payload = await response.json() as ApiResponse<WorkspaceAssistantRunDetail>
  return payload.data
}

function generatedDraftFromAction(action: WorkspaceToolLog, projectId: string): GeneratedChapterDraft | null {
  if (!['chapter_writer', 'save_external_chapter_draft'].includes(String(action.tool || ''))) return null
  const data = action.data || {}
  const draftId = String(data.draft_id || '')
  const content = String(data.content || '')
  if (!draftId || !content) return null
  return {
    draftId,
    projectId: String(data.project_id || projectId),
    title: String(data.title || ''),
    outlineNodeId: data.outline_node_id ? String(data.outline_node_id) : null,
    contextManifestId: data.context_manifest_id ? String(data.context_manifest_id) : null,
    savedChapterId: data.saved_chapter_id ? String(data.saved_chapter_id) : null,
    content,
    wordCount: Number(data.word_count || 0),
    status: String(data.draft_status || 'pending') as GeneratedChapterDraft['status'],
  }
}

function WorkspaceAssistantChat({
  projectId,
  selectedText,
  selectedTextChapterId,
  defaultModel,
  modelOptions = [],
  modelsLoading = false,
  onTaskModelChange,
  onManageModels,
  onApplied,
}: WorkspaceAssistantChatProps) {
  const navigate = useNavigate()
  const {
    generatedDraft,
    openGeneratedDraft,
    updateGeneratedDraft,
    triggerRefresh,
  } = useAiPanelContext()
  const [conversations, setConversations] = useState<WorkspaceAssistantConversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<WorkspaceAssistantMessage[]>([])
  // Query this project's cataloging operations directly.  A global "latest
  // 50" query could evict the relevant post-write task on busy installations.
  const { data: operations = [], refetch: refetchCatalogingOperations } = useOperations(100, {
    projectId,
    sourceKind: 'cataloging',
  })
  const [input, setInput] = useState('')
  const [generating, setGenerating] = useState(false)

  const [historyLoading, setHistoryLoading] = useState(false)
  const [runLogs, setRunLogs] = useState<WorkspaceRunLog[]>([])
  const [currentRun, setCurrentRun] = useState<WorkspaceAssistantRun | null>(null)
  const [showAllRunLogs, setShowAllRunLogs] = useState(false)
  const [showSelectionTag, setShowSelectionTag] = useState(true)
  const [retryingStepId, setRetryingStepId] = useState<string | null>(null)
  const [detailStep, setDetailStep] = useState<StepDetail | null>(null)
  useEffect(() => {
    setShowSelectionTag(true)
  }, [selectedText])
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const [showScrollBottom, setShowScrollBottom] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const cancelRequestedRef = useRef(false)
  const cancelInFlightRef = useRef<Promise<boolean> | null>(null)
  const executionSequenceRef = useRef(0)
  const activeExecutionRef = useRef<ActiveAssistantExecution | null>(null)
  const resumePersistedRunRef = useRef<(
    detail: WorkspaceAssistantRunDetail,
    conversationId: string,
    assistantMessageId: string,
  ) => void>(() => undefined)
  const mountedRef = useRef(false)
  const [memoryModalOpen, setMemoryModalOpen] = useState(false)
  const [modelChanging, setModelChanging] = useState(false)
  const [canceling, setCanceling] = useState(false)
  const [cancelPending, setCancelPending] = useState(false)
  const [runtimeAnnouncement, setRuntimeAnnouncement] = useState('')

  const selectedProvider = String(defaultModel || '').split(':', 1)[0]
  const isLocalCliModel = selectedProvider.endsWith('_cli')
  const isOpenCodeCliModel = selectedProvider === 'opencode_cli'
  const catalogingMessages = useMemo(
    () => projectCatalogingMessages(operations, projectId),
    [operations, projectId],
  )
  const displayedMessages = useMemo(
    // Task notices are a live status lane rather than persisted dialogue.
    // Keep them after the conversation so a notice created during tool
    // execution cannot disappear above the writer's final response.
    () => [
      ...sortWorkspaceMessages(messages),
      ...sortWorkspaceMessages(catalogingMessages),
    ],
    [messages, catalogingMessages],
  )

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      // This only closes the browser subscriber. The detached backend task is
      // deliberately left running and remains available from the task centre.
      activeExecutionRef.current?.controller.abort()
      activeExecutionRef.current = null
      abortRef.current = null
      cancelRequestedRef.current = false
      cancelInFlightRef.current = null
    }
  }, [])

  const changeTaskModel = async (nextModel?: string) => {
    if (!onTaskModelChange || nextModel === defaultModel) return
    setModelChanging(true)
    try {
      await onTaskModelChange(nextModel)
      message.success(generating
        ? '项目助手默认模型已保存；当前任务仍使用启动时的模型，下个任务生效'
        : nextModel ? '项目助手默认模型已保存' : '项目助手将跟随全局默认模型')
    } catch (err: any) {
      message.error(err.message || '切换项目助手模型失败，仍保留原模型')
    } finally {
      setModelChanging(false)
    }
  }

  const saveChapterDraft = useCallback(async (
    action: WorkspaceToolLog,
    mode: 'save_only' | 'save_and_catalog',
  ) => {
    const actionDraft = generatedDraftFromAction(action, projectId)
    const draft = generatedDraft?.draftId === actionDraft?.draftId
      ? generatedDraft
      : actionDraft
    if (!draft) {
      message.error('找不到可保存的章节草稿，请重新生成')
      return
    }
    if (draft.status !== 'pending') {
      message.info('这份草稿已经保存')
      return
    }
    const payload = {
      title: draft.title.trim() || '未命名章节',
      outline_node_id: draft.outlineNodeId,
      content: draft.content,
      context_manifest_id: draft.contextManifestId,
      draft_id: draft.draftId,
      cataloging_mode: mode,
      trigger_type: 'ai_insert',
    }
    let response: { data: ApiResponse<Record<string, any>> }
    try {
      response = await apiClient.post<ApiResponse<Record<string, any>>>(
        `/projects/${projectId}/chapters`,
        payload,
      )
    } catch (error) {
      try {
        const pending = await apiClient.get<ApiResponse<{ draft_id?: string } | null>>(
          `/projects/${projectId}/chapter-drafts/pending`,
        )
        if (String(pending.data.data?.draft_id || '') !== draft.draftId) {
          updateGeneratedDraft({ status: 'superseded' })
          triggerRefresh()
        }
      } catch {
        // Keep the local draft when the authoritative state cannot be checked.
      }
      throw error
    }
    const chapterId = String(response.data.data.id || response.data.data.chapter_id || '')
    const savedDraft = {
      ...draft,
      savedChapterId: chapterId || null,
      status: 'saved' as const,
    }
    if (generatedDraft?.draftId === draft.draftId) updateGeneratedDraft(savedDraft)
    else openGeneratedDraft(savedDraft)
    triggerRefresh()
    await Promise.resolve(onApplied?.())
    navigate(`/project/${encodeURIComponent(projectId)}`)
    if (mode === 'save_and_catalog') {
      if (response.data.data.cataloging_job?.started) {
        await refetchCatalogingOperations()
        message.success('章节已保存，建档已经开始；可从聊天中的按钮查看进度')
      } else {
        message.error(response.data.data.cataloging_job?.error || '章节已保存，但建档启动失败；请在正文页重试')
      }
    } else {
      message.success('章节已仅保存；完成建档前 AI 不会继续下一章')
    }
  }, [
    generatedDraft,
    navigate,
    onApplied,
    openGeneratedDraft,
    projectId,
    refetchCatalogingOperations,
    triggerRefresh,
    updateGeneratedDraft,
  ])

  const handleMessagesScroll = useCallback(() => {
    const el = messagesRef.current
    if (!el) return
    const threshold = el.scrollHeight - el.clientHeight - 60
    setShowScrollBottom(el.scrollTop < threshold)
  }, [])

  const scrollToBottom = () => {
    messagesRef.current?.scrollTo({
      top: messagesRef.current.scrollHeight,
      behavior: motionAwareScrollBehavior(),
    })
  }

  const latestCatalogingNotice = catalogingMessages[catalogingMessages.length - 1]
  const latestCatalogingNoticeKey = latestCatalogingNotice
    ? `${latestCatalogingNotice.id}:${latestCatalogingNotice.status}:${latestCatalogingNotice.created_at}`
    : ''
  useEffect(() => {
    if (!latestCatalogingNoticeKey || showScrollBottom) return
    const frame = window.requestAnimationFrame(() => {
      messagesRef.current?.scrollTo({
        top: messagesRef.current.scrollHeight,
        behavior: motionAwareScrollBehavior(),
      })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [latestCatalogingNoticeKey, showScrollBottom])

  const agentRuntimeTool = 'project_agent'
  const scopeLabel = SCOPE_LABEL

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

  const isCurrentExecution = (execution: ActiveAssistantExecution) => (
    mountedRef.current && activeExecutionRef.current?.token === execution.token
  )

  const updateAssistantById = (
    assistantMessageId: string,
    updater: (message: WorkspaceAssistantMessage) => WorkspaceAssistantMessage,
  ) => {
    setMessages((prev) => {
      const next = [...prev]
      const index = next.findIndex((item) => item.role === 'assistant' && item.id === assistantMessageId)
      if (index < 0) return prev
      next[index] = updater(next[index])
      return next
    })
  }

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

  const appendToolLog = (log: WorkspaceToolLog, content?: string, assistantMessageId?: string) => {
    const updater = (item: WorkspaceAssistantMessage) => {
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
    }
    if (assistantMessageId) updateAssistantById(assistantMessageId, updater)
    else updateLatestAssistant(updater)
  }

  const fetchConversations = useCallback(async () => {
    try {
      const res = await apiClient.get<ApiResponse<{ items: WorkspaceAssistantConversation[]; total: number }>>(
        `/projects/${projectId}/ai/assistant/conversations`,
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
      const lastRunMessage = [...loadedMessages]
        .reverse()
        .find((item) => item.role === 'assistant' && item.data?.run)
      const lastRun = lastRunMessage?.data?.run || null
      upsertConversation(res.data.data.conversation)
      setInput('')
      setRunLogs([])
      setShowAllRunLogs(false)
      if (lastRun) {
        const detail = await refreshRunLogs(lastRun.id)
        if (detail.assistant_message && TERMINAL_RUN_STATUSES.has(detail.run.status)) {
          const persistedMessage = toWorkspaceMessage(detail.assistant_message)
          setMessages((current) => current.map((item) => (
            item.id === lastRunMessage?.id ? persistedMessage : item
          )))
        } else if (
          (detail.run.status === 'queued' || detail.run.status === 'running')
          && lastRunMessage?.id
        ) {
          resumePersistedRunRef.current(
            detail,
            res.data.data.conversation.id,
            lastRunMessage.id,
          )
        }
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
    fetchConversations().then((items) => {
      if (mounted && items[0]) {
        loadConversation(items[0].id)
      }
    })
    return () => {
      mounted = false
      // Don't abort on unmount — backend will finish processing and commit results
    }
  }, [fetchConversations, loadConversation])

  const startNewConversation = () => {
    if (generating) {
      message.info('请先停止或等待当前任务完成，再新建对话')
      return
    }
    setActiveConversationId(null)
    setMessages([])
    setInput('')
    setRunLogs([])
    setCurrentRun(null)
    setShowAllRunLogs(false)
  }

  const deleteConversation = (conversationId: string) => {
    if (generating) {
      message.info('请先停止或等待当前任务完成，再删除对话')
      return
    }
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

  const cancelAssistantRun = (
    run: WorkspaceAssistantRun,
    execution: ActiveAssistantExecution,
  ): Promise<boolean> => {
    if (!run.operation_id || !isCurrentExecution(execution)) return Promise.resolve(false)
    if (cancelInFlightRef.current) return cancelInFlightRef.current

    const request = (async () => {
      setCanceling(true)
      setCancelPending(true)
      setRuntimeAnnouncement('正在取消任务')
      try {
        await apiClient.post(`/operations/${run.operation_id}/cancel`)
        if (!isCurrentExecution(execution)) return true
        cancelRequestedRef.current = false
        const cancelledRun: WorkspaceAssistantRun = { ...run, status: 'cancelled', phase: 'cancelled' }
        execution.run = cancelledRun
        execution.terminalHandled = true
        setCurrentRun(cancelledRun)
        setGenerating(false)
        const cancelledMessage = '已停止后续执行；取消前已完成的内容会保留。'
        addRunLog({ tool: agentRuntimeTool, status: 'cancelled', message: cancelledMessage })
        updateAssistantById(execution.assistantMessageId, (item) => ({
          ...item,
          content: item.content && item.content !== '正在分析需求...'
            ? `${item.content}\n\n（${cancelledMessage}）`
            : cancelledMessage,
          status: 'aborted',
        }))
        setRuntimeAnnouncement(cancelledMessage)
        message.success(cancelledMessage)
        execution.controller.abort()
        return true
      } catch (error: any) {
        if (!isCurrentExecution(execution)) return false
        const status = error?.response?.status
        if (status === 409) {
          try {
            const detail = await fetchAssistantRunDetail(projectId, run.id, execution.controller.signal)
            const terminal = await applyRecoveredRunDetail(detail, execution)
            cancelRequestedRef.current = false
            if (terminal) {
              setGenerating(false)
              setRuntimeAnnouncement('任务已结束，已恢复服务器中的实际结果')
              message.info('任务已结束，已显示实际结果')
              execution.controller.abort()
              return true
            }
            message.warning('服务器仍显示任务在执行，可在任务中心再次取消')
          } catch (reconcileError) {
            if (!isAbortError(reconcileError)) {
              message.error('无法确认任务状态，请在任务中心查看')
            }
          }
        } else {
          const failureMessage = error?.message || '取消任务失败，请在任务中心重试'
          message.error(failureMessage)
          addRunLog({ tool: agentRuntimeTool, status: 'error', message: failureMessage })
        }
        cancelRequestedRef.current = false
        return false
      } finally {
        cancelInFlightRef.current = null
        if (isCurrentExecution(execution)) {
          setCanceling(false)
          setCancelPending(false)
        }
      }
    })()
    cancelInFlightRef.current = request
    return request
  }

  const stopGeneration = () => {
    if (cancelRequestedRef.current || cancelInFlightRef.current) return
    const execution = activeExecutionRef.current
    if (!execution) return
    cancelRequestedRef.current = true
    setCancelPending(true)
    setRuntimeAnnouncement('正在等待任务编号并取消')
    if (execution.run?.operation_id) {
      void cancelAssistantRun(execution.run, execution)
      return
    }
    addRunLog({ tool: agentRuntimeTool, status: 'running', message: '正在取得任务编号，随后立即取消' })
    message.info('任务正在建立，取得任务编号后会立即取消')
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

  const applyRecoveredRunDetail = async (
    detail: WorkspaceAssistantRunDetail,
    execution: ActiveAssistantExecution,
  ) => {
    if (!isCurrentExecution(execution)) return false
    const run = detail.run
    execution.run = run
    setCurrentRun(run)
    if (detail.steps?.length) {
      setRunLogs(detail.steps.map(runStepToLog))
    }
    const terminal = TERMINAL_RUN_STATUSES.has(run.status)
    if (!terminal) return false

    if (detail.assistant_message) {
      const persistedMessage = toWorkspaceMessage(detail.assistant_message)
      updateAssistantById(execution.assistantMessageId, () => persistedMessage)
      execution.assistantMessageId = detail.assistant_message.id
    } else if (run.error) {
      updateAssistantById(execution.assistantMessageId, (item) => ({
        ...item,
        content: run.error || item.content,
        status: 'error',
      }))
    }

    if (!execution.terminalHandled) {
      execution.terminalHandled = true
      const ok = run.status === 'completed'
      const terminalMessage = ok
        ? '后台任务已完成并恢复结果'
        : run.status === 'interrupted'
          ? '后台任务已中断，可安全重试'
          : `后台任务${RUN_STATUS_LABELS[run.status]}`
      addRunLog({ tool: agentRuntimeTool, status: ok ? 'ok' : run.status, message: terminalMessage })
      setRuntimeAnnouncement(terminalMessage)
      await fetchConversations()
      if (ok) await Promise.resolve(onApplied?.())
    }
    return true
  }

  async function reconcileDetachedRun(
    initialRun: WorkspaceAssistantRun | null,
    execution: ActiveAssistantExecution,
    announce = true,
  ) {
    if (!isCurrentExecution(execution)) return false
    if (!initialRun) {
      const missingRunMessage = '连接在任务编号返回前中断，无法自动恢复。请在任务中心查看执行结果。'
      addRunLog({ tool: agentRuntimeTool, status: 'error', message: missingRunMessage })
      setRuntimeAnnouncement(missingRunMessage)
      updateAssistantById(execution.assistantMessageId, (item) => ({
        ...item,
        content: item.content && item.content !== '正在分析需求...'
          ? `${item.content}\n\n${missingRunMessage}`
          : missingRunMessage,
        status: 'error',
      }))
      return false
    }

    if (announce) {
      const reconnectingMessage = '连接中断，任务仍在后台执行。正在恢复状态…'
      addRunLog({ tool: agentRuntimeTool, status: 'running', message: reconnectingMessage })
      setRuntimeAnnouncement(reconnectingMessage)
      updateAssistantById(execution.assistantMessageId, (item) => ({
        ...item,
        content: item.content || reconnectingMessage,
        status: 'running',
      }))
    }

    let run = initialRun
    let consecutiveFailures = 0
    while (isCurrentExecution(execution) && !execution.controller.signal.aborted) {
      try {
        const detail = await fetchAssistantRunDetail(projectId, run.id, execution.controller.signal)
        consecutiveFailures = 0
        run = detail.run
        if (await applyRecoveredRunDetail(detail, execution)) return true
      } catch (error) {
        if (isAbortError(error) || !isCurrentExecution(execution)) return false
        consecutiveFailures += 1
        const status = error instanceof AssistantRunRequestError ? error.status : undefined
        const fatal = status !== undefined && status >= 400 && status < 500
        if (fatal || consecutiveFailures >= MAX_RUN_POLL_FAILURES) {
          const recoveryMessage = fatal
            ? `${error instanceof Error ? error.message : '恢复任务状态失败'}。请在任务中心查看结果。`
            : '多次恢复连接失败，已停止本页轮询。后台任务不受影响，请在任务中心查看。'
          addRunLog({ tool: agentRuntimeTool, status: 'error', message: recoveryMessage })
          setRuntimeAnnouncement(recoveryMessage)
          updateAssistantById(execution.assistantMessageId, (item) => ({
            ...item,
            content: item.content.includes(recoveryMessage)
              ? item.content
              : `${item.content}\n\n${recoveryMessage}`.trim(),
            status: 'error',
          }))
          return false
        }
      }

      try {
        await abortableDelay(Math.min(4000, 750 * (2 ** consecutiveFailures)), execution.controller.signal)
      } catch (error) {
        if (isAbortError(error)) return false
        throw error
      }
    }
    return false
  }

  function resumePersistedRun(
    detail: WorkspaceAssistantRunDetail,
    conversationId: string,
    assistantMessageId: string,
  ) {
    const existingExecution = activeExecutionRef.current
    if (existingExecution?.run?.id === detail.run.id) return

    existingExecution?.controller.abort()
    const controller = new AbortController()
    const execution: ActiveAssistantExecution = {
      token: ++executionSequenceRef.current,
      controller,
      userMessageId: '',
      assistantMessageId,
      conversationId,
      run: detail.run,
      terminalHandled: false,
    }
    activeExecutionRef.current = execution
    abortRef.current = controller
    cancelRequestedRef.current = false
    setCancelPending(false)
    setCanceling(false)
    setGenerating(true)
    setRuntimeAnnouncement('已恢复正在后台执行的任务，可继续等待或取消')

    void (async () => {
      try {
        const terminal = await applyRecoveredRunDetail(detail, execution)
        if (!terminal) {
          await reconcileDetachedRun(detail.run, execution, false)
        }
      } finally {
        if (isCurrentExecution(execution)) {
          setGenerating(false)
          setCanceling(false)
          setCancelPending(false)
          cancelRequestedRef.current = false
          activeExecutionRef.current = null
          if (abortRef.current === controller) abortRef.current = null
        }
      }
    })()
  }
  resumePersistedRunRef.current = resumePersistedRun

  const sendMessage = async (options?: {
    text?: string
    readPaths?: string[]
  }) => {
    if (generating || historyLoading) {
      message.info(generating ? '当前任务仍在执行' : '正在加载对话，请稍候')
      return
    }
    const userText = String(options?.text ?? input).trim()
    if (!userText) {
      message.warning('请输入要发送给AI的内容')
      return
    }

    const proposedReadPaths = isOpenCodeCliModel
      ? (options?.readPaths ?? extractExplicitLocalPaths(userText))
      : []
    if (
      isOpenCodeCliModel
      && options?.readPaths === undefined
      && proposedReadPaths.length > 0
    ) {
      Modal.confirm({
        title: '仅允许 OpenCode 读取这些路径一次？',
        okText: '创建只读快照并发送',
        cancelText: '取消',
        content: (
          <div className="workspace-assistant-cli-grant-copy">
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
    const grantedReadPaths = isOpenCodeCliModel ? proposedReadPaths : []

    setGenerating(true)
    cancelRequestedRef.current = false
    setRunLogs([{ key: `${Date.now()}-start`, tool: agentRuntimeTool, status: 'running', message: '正在提交给AI助手' }])
    setCurrentRun(null)
    setShowAllRunLogs(false)
    const controller = new AbortController()
    const token = ++executionSequenceRef.current
    const execution: ActiveAssistantExecution = {
      token,
      controller,
      userMessageId: `pending-user-${token}`,
      assistantMessageId: `pending-assistant-${token}`,
      conversationId: activeConversationId,
      run: null,
      terminalHandled: false,
    }
    activeExecutionRef.current = execution
    abortRef.current = controller
    setMessages((prev) => [
      ...prev,
      { id: execution.userMessageId, role: 'user', content: userText, status: 'completed' },
      {
        id: execution.assistantMessageId,
        role: 'assistant',
        content: '正在分析需求...',
        status: 'running',
        data: createEmptyWorkspaceResponse([{ tool: agentRuntimeTool, status: 'running', detail: 'AI 正在搜索和分析...' }]),
      },
    ])
    if (options?.text === undefined) setInput('')

    try {
      const history = messages.slice(-8).map((item) => ({
        role: item.role,
        content: item.content,
      }))
      const res = await fetch(`/api/v1/projects/${projectId}/ai/workspace-assistant/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          outline_batch_count: 3,
          message: userText,
          conversation_id: activeConversationId || undefined,
          selected_text: selectedText || undefined,
          selected_text_chapter_id: selectedTextChapterId || undefined,
          model: defaultModel || undefined,
          temperature: 0.3,
          max_tokens: undefined,
          local_cli_read_permission_grant: grantedReadPaths.length > 0 ? 'read_once' : 'none',
          local_cli_read_paths: grantedReadPaths,
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
        if (!isCurrentExecution(execution)) return
        if (event.type === 'conversation') {
          const conversation = event.conversation as WorkspaceAssistantConversation
          const persistedUser = event.user_message as WorkspacePersistedMessage
          const persistedAssistant = event.assistant_message as WorkspacePersistedMessage
          const pendingUserId = execution.userMessageId
          const pendingAssistantId = execution.assistantMessageId
          execution.conversationId = conversation.id
          execution.userMessageId = persistedUser.id
          execution.assistantMessageId = persistedAssistant.id
          setActiveConversationId(conversation.id)
          upsertConversation(conversation)
          setMessages((prev) => {
            const withoutPendingOrDuplicate = prev.filter((item) => (
              item.id !== pendingUserId
              && item.id !== pendingAssistantId
              && item.id !== persistedUser.id
              && item.id !== persistedAssistant.id
            ))
            return sortWorkspaceMessages([
              ...withoutPendingOrDuplicate,
              toWorkspaceMessage(persistedUser),
              toWorkspaceMessage(persistedAssistant),
            ])
          })
        } else if (event.type === 'run') {
          const run = event.run as WorkspaceAssistantRun
          execution.run = run
          setCurrentRun(run)
          addRunLog({ tool: 'run', status: run.status || 'running', message: `任务已创建：${run.id.slice(0, 8)}` })
          if (cancelRequestedRef.current && run.operation_id) {
            void cancelAssistantRun(run, execution)
          }
        } else if (event.type === 'status') {
          const detail = event.message || '正在执行'
          const log = { tool: event.tool || agentRuntimeTool, status: 'running', detail, stepId: event.step_id }
          addRunLog({ tool: log.tool, status: log.status, message: detail, stepId: event.step_id })
          appendToolLog(log, `正在执行：${detail}`, execution.assistantMessageId)
        } else if (event.type === 'tool') {
          const detail = event.detail || event.message || event.tool
          const log = { tool: event.tool || 'tool', status: event.status || 'ok', detail, stepId: event.step_id }
          addRunLog({ tool: log.tool, status: log.status, message: `${log.tool}: ${detail}`, stepId: event.step_id })
          appendToolLog(log, undefined, execution.assistantMessageId)
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
          appendToolLog({ tool: ev.tool, status, detail, stepId: ev.step_id }, undefined, execution.assistantMessageId)
        } else if (event.type === 'content_delta') {
          const ev = event as { delta: string }
          updateAssistantById(execution.assistantMessageId, (item) => ({
              ...item,
              content: item.content === '正在分析需求...'
                ? ev.delta
                : item.content + ev.delta,
            }))
        } else if (event.type === 'reasoning_delta') {
          const ev = event as { delta: string }
          updateAssistantById(execution.assistantMessageId, (item) => ({
            ...item,
            reasoning_content: `${item.reasoning_content || ''}${ev.delta || ''}`,
          }))
        } else if (event.type === 'complete') {
          const payload = event.data as WorkspaceAssistantResponse
          const reply = payload.reply?.trim() || EMPTY_ASSISTANT_REPLY
          const draftAction = [...(payload.applied_actions || [])]
            .reverse()
            .find((action) => generatedDraftFromAction(action, projectId) !== null)
          const nextDraft = draftAction ? generatedDraftFromAction(draftAction, projectId) : null
          if (nextDraft?.status === 'pending') {
            openGeneratedDraft(nextDraft)
            navigate(`/project/${encodeURIComponent(projectId)}`)
          }
          completed = true
          execution.terminalHandled = true
          if (payload.run) {
            execution.run = payload.run
            setCurrentRun(payload.run)
          }
          upsertConversation(payload.conversation)
          const assistantMessageId = execution.assistantMessageId
          updateAssistantById(assistantMessageId, (item) => ({
              id: payload.message?.id || item.id,
              conversation_id: payload.message?.conversation_id || item.conversation_id,
              role: 'assistant',
              content: reply,
              reasoning_content: payload.reasoning_content || item.reasoning_content,
              status: payload.message?.status || 'completed',
              created_at: payload.message?.created_at || item.created_at,
              updated_at: payload.message?.updated_at || item.updated_at,
              data: payload,
            }))
          if (payload.message?.id) execution.assistantMessageId = payload.message.id
          addRunLog(assistantOutcomeToRunLog(payload, agentRuntimeTool))
          setRuntimeAnnouncement('任务已完成')
          void fetchConversations()
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
        await reconcileDetachedRun(execution.run, execution)
      }
    } catch (err: any) {
      if (!isAbortError(err)) {
        if (execution.run) {
          await reconcileDetachedRun(execution.run, execution)
        } else {
          const failureMessage = err.message || 'AI助手执行失败'
          message.error(failureMessage)
          addRunLog({ tool: agentRuntimeTool, status: 'error', message: failureMessage })
          setRuntimeAnnouncement(failureMessage)
          updateAssistantById(execution.assistantMessageId, (item) => ({ ...item, content: failureMessage, status: 'error' }))
        }
      }
    } finally {
      if (isCurrentExecution(execution)) {
        setGenerating(false)
        setCanceling(false)
        setCancelPending(false)
        cancelRequestedRef.current = false
        activeExecutionRef.current = null
        if (abortRef.current === controller) abortRef.current = null
      }
    }
  }

  const modelUnavailable = !modelsLoading && modelOptions.length === 0

  return (
    <section className="workspace-assistant-chat" data-testid="project-ai-chat">
      <div className="workspace-assistant-sr-status" role="status" aria-live="polite" aria-atomic="true">
        {runtimeAnnouncement}
      </div>
      <div className="workspace-assistant-head">
        <div className="workspace-assistant-head-primary">
          <Text strong>{scopeLabel}</Text>
          <Space size={4}>
            <Button size="small" onClick={() => setMemoryModalOpen(true)}>记忆</Button>
            <Button
              aria-label="刷新对话"
              size="small"
              icon={<ReloadOutlined />}
              loading={historyLoading}
              disabled={generating}
              onClick={fetchConversations}
            />
            <Button
              size="small"
              type="primary"
              icon={<PlusOutlined />}
              disabled={generating || modelUnavailable}
              onClick={startNewConversation}
            >新对话</Button>
          </Space>
        </div>
        <div className="workspace-assistant-model-row">
          <Tooltip title="这是项目助手任务的默认模型。清空后跟随全局默认；切换不会改变已经启动的任务。">
            <span className="workspace-assistant-model-label">
              助手模型 <InfoCircleOutlined aria-hidden="true" />
            </span>
          </Tooltip>
          <Select
            aria-label="项目助手模型"
            className="workspace-assistant-model-select"
            size="small"
            allowClear
            value={defaultModel}
            options={modelOptions}
            loading={modelsLoading || modelChanging}
            disabled={modelOptions.length === 0 || modelChanging}
            placeholder={modelsLoading ? '正在读取可用模型' : '暂无可用模型'}
            popupMatchSelectWidth={false}
            onChange={(value) => { void changeTaskModel(value) }}
          />
          <Tooltip title={modelOptions.length === 0 ? '前往设置并完成模型真实对话测试' : '管理模型配置'}>
            <Button
              aria-label="管理模型"
              size="small"
              type="text"
              icon={<SettingOutlined />}
              onClick={onManageModels}
            />
          </Tooltip>
        </div>
      </div>

      {modelUnavailable && (
        <Alert
          className="workspace-assistant-readiness"
          type="warning"
          showIcon
          message="先准备一个可用模型"
          description="完成一次真实对话测试后，就可以在这里讨论剧情、调用工具并写入作品。"
          action={<Button size="small" onClick={onManageModels}>配置模型</Button>}
        />
      )}

      {isLocalCliModel && !modelUnavailable && (
        <Alert
          className="workspace-assistant-cli-permission"
          type="info"
          showIcon
          message="本机 CLI 已连接本轮临时 Siming MCP"
          description={isOpenCodeCliModel
            ? '只开放当前作品范围的工具；本地路径仍需另行确认并只提供临时只读快照。'
            : '只开放当前作品范围的工具，启动参数已预先批准本轮 MCP；不会修改 CLI 的全局配置。'}
        />
      )}

      <div className="workspace-assistant-history">
        {conversations.length > 0 ? conversations.map((conversation) => (
          <div
            key={conversation.id}
            className={`workspace-assistant-history-item${conversation.id === activeConversationId ? ' workspace-assistant-history-item-active' : ''}`}
          >
            <button
              aria-current={conversation.id === activeConversationId ? 'true' : undefined}
              className="workspace-assistant-history-select"
              disabled={generating}
              type="button"
              onClick={() => loadConversation(conversation.id)}
            >
              <span title={conversation.title}>{conversation.title}</span>
            </button>
            <Button
              aria-label={`删除对话：${conversation.title}`}
              type="text"
              size="small"
              danger
              disabled={generating}
              icon={<DeleteOutlined />}
              onClick={() => deleteConversation(conversation.id)}
            />
          </div>
        )) : (
          <Text type="secondary" style={{ fontSize: 12 }}>还没有历史对话。</Text>
        )}
      </div>

      {runLogs.length > 0 && (
        <div className="workspace-assistant-run-log">
          <div className="workspace-assistant-run-log-header">
            <Space size={6}>
              <Text type="secondary" style={{ fontSize: 12 }}>运行过程</Text>
              {currentRun && (
                <Tag color={
                  currentRun.status === 'completed' ? 'green'
                    : currentRun.status === 'error' || currentRun.status === 'interrupted' ? 'red'
                      : currentRun.status === 'cancelled' || currentRun.status === 'aborted' ? 'default'
                        : 'blue'
                }>
                  {RUN_STATUS_LABELS[currentRun.status]} #{currentRun.id.slice(0, 8)}
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
                log.status === 'blocked' ? 'orange' :
                log.status === 'skipped' ? 'orange' : 'blue'
              }>
                {log.status === 'error' && log.resolvedStepId ? '已解决' :
                 log.attemptNo && log.attemptNo > 1 ? `重试 #${log.attemptNo}` :
                 log.status || 'running'}
              </Tag>
              {log.tool && <Text code>{log.tool}</Text>}
              <Text>{log.message}</Text>
              {log.status === 'error' && !log.resolvedStepId && log.canRetry === false && log.retryBlockReason && (
                <Text type="secondary">{log.retryBlockReason}</Text>
              )}
              {log.status === 'error' && !log.resolvedStepId && log.canRetry !== false && log.stepId && currentRun && (
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
        messages={displayedMessages}
        generating={generating}
        showScrollBottom={showScrollBottom}
        onScrollToBottom={scrollToBottom}
        messagesRef={messagesRef}
        onScroll={handleMessagesScroll}
        projectId={projectId}
        onSaveChapterDraft={saveChapterDraft}
        activeDraftId={generatedDraft?.draftId || null}
        activeDraftStatus={generatedDraft?.status || null}
        emptyDescription={modelUnavailable ? '模型准备好后，从这里开始第一次对话。' : undefined}
        onStorageRepaired={() => {
          onApplied?.()
        }}
      />

      <Composer
        input={input}
        generating={generating}
        disabled={modelUnavailable}
        disabledPlaceholder="请先配置并验证一个可用模型"
        cancelPending={cancelPending || canceling}
        selectedText={selectedText}
        showSelectionTag={showSelectionTag}
        messageCount={displayedMessages.length}
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
