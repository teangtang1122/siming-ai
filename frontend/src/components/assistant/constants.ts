/* Constants and pure helper functions for the assistant chat. */
import type {
  WorkspaceAssistantMessage,
  WorkspaceAssistantResponse,
  WorkspaceAssistantRunStep,
  WorkspacePersistedMessage,
  WorkspaceRunLog,
  WorkspaceToolLog,
} from './types'

export const DEFAULT_FORBIDDEN_SENTENCE_PATTERNS = [
  '不是……是……', '不是……而是……', '不是……却是……', '与其说……不如说……',
  '在……中……', '在……时……', '随着……',
  '仿佛……', '似乎……',
  '只见……', '只听得……', '不由得……', '不禁……', '忍不住……',
  '这一切都说明……', '从那天起……', '此后……',
  '与此同时……', '另一方面……',
  '很愤怒', '感到悲伤', '感到恐惧', '显得很……',
  '他的眼中……', '她的心里……',
  '深深地', '无比', '极其',
  '一股……', '一种……的感觉', '令人……', '让人……',
  '充满了', '充斥着',
  '缓缓地', '默默地', '静静地', '淡淡地', '微微……',
  '然而', '于是', '突然', '忽然', '终于', '其实',
  '总之', '无论如何', '毋庸置疑', '某种程度上', '某种意义上',
].join('\n')

export const DEFAULT_RHETORIC_GUIDELINES =
  '克制使用比喻、拟人、排比等修辞，禁止连续堆叠比喻。优先用具体动作、感官细节、因果推进和角色反应来表达画面与情绪。非必要不使用抽象概念比喻；同一段落不要出现多个比喻。'

export const SCOPE_LABEL = '项目助手'
export const INPUT_PLACEHOLDER =
  '告诉AI你想写什么，或让它检查剧情、规划大纲、补全角色世界观、创建章节...'

export function mergeForbiddenPatterns(userPatterns: string): string {
  const defaults = new Set(
    DEFAULT_FORBIDDEN_SENTENCE_PATTERNS.split('\n').map((s) => s.trim()).filter(Boolean),
  )
  const user = userPatterns.split('\n').map((s) => s.trim()).filter(Boolean)
  return [...new Set([...defaults, ...user])].join('\n')
}

export function stripDefaults(patterns: string): string {
  const defaults = new Set(
    DEFAULT_FORBIDDEN_SENTENCE_PATTERNS.split('\n').map((s) => s.trim()).filter(Boolean),
  )
  return patterns
    .split('\n')
    .map((s) => s.trim())
    .filter((s) => s && !defaults.has(s))
    .join('\n')
}

export const createEmptyWorkspaceResponse = (
  toolLogs: WorkspaceToolLog[] = [],
): WorkspaceAssistantResponse => ({
  reply: '',
  actions: [],
  applied_actions: [],
  tool_logs: toolLogs,
})

export const runStepToLog = (step: WorkspaceAssistantRunStep): WorkspaceRunLog => ({
  key: step.id,
  tool: step.tool || step.step_type || 'step',
  status: step.status || 'running',
  message: step.detail || step.error || step.tool || step.step_type || '步骤',
  stepId: step.id,
})

export function assistantOutcomeToRunLog(
  payload: WorkspaceAssistantResponse,
  tool: string,
): Omit<WorkspaceRunLog, 'key'> {
  switch (payload.outcome) {
    case 'completed_with_reply':
      return { tool, status: 'ok', message: 'AI助手已回复' }
    case 'completed_with_tools':
      return { tool, status: 'ok', message: 'AI助手已完成工具操作' }
    case 'empty_response':
      return { tool, status: 'skipped', message: '模型没有返回文字或工具结果' }
    case 'skipped_preflight':
      return { tool, status: 'skipped', message: '预检已跳过执行，等待补充信息' }
    case 'blocked':
      return { tool, status: 'blocked', message: '任务已阻塞，等待确认或修复' }
    case 'failed':
      return { tool, status: 'error', message: '任务执行失败，部分数据可能未保存' }
    default:
      return { tool, status: 'ok', message: payload.reply?.trim() ? 'AI助手已回复' : 'AI助手已完成' }
  }
}

const messageTime = (message: WorkspaceAssistantMessage) => {
  const value = message.created_at || message.updated_at || ''
  const parsed = value ? new Date(value).getTime() : Number.NaN
  return Number.isFinite(parsed) ? parsed : Number.MAX_SAFE_INTEGER
}

export const sortWorkspaceMessages = (items: WorkspaceAssistantMessage[]) =>
  [...items].sort((a, b) => {
    const timeDiff = messageTime(a) - messageTime(b)
    if (timeDiff !== 0) return timeDiff
    if (a.role !== b.role) return a.role === 'user' ? -1 : 1
    return String(a.id || '').localeCompare(String(b.id || ''))
  })

export const toWorkspaceMessage = (
  item: WorkspacePersistedMessage,
): WorkspaceAssistantMessage => ({
  id: item.id,
  conversation_id: item.conversation_id,
  role: item.role,
  content: item.content,
  status: item.status,
  created_at: item.created_at || undefined,
  updated_at: item.updated_at || undefined,
  data:
    item.role === 'assistant' && item.payload
      ? {
          ...createEmptyWorkspaceResponse(),
          ...item.payload,
          tool_logs: item.payload.tool_logs || [],
          actions: item.payload.actions || [],
          applied_actions: item.payload.applied_actions || [],
        }
      : undefined,
})
