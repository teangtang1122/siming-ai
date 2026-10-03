import type { OperationRun } from '../../shared/api/contracts'
import { apiDateTimeMs } from '../../utils/dateTime'

export type DesktopPetVisualState =
  | 'idle'
  | 'thinking'
  | 'writing'
  | 'waiting_author'
  | 'completed'
  | 'error'
  | 'sleeping'

export const DESKTOP_PET_LONG_IDLE_MS = 10 * 60_000

export type DesktopPetTone = 'completed' | 'error'

export function desktopPetToneForState(
  state: DesktopPetVisualState,
): DesktopPetTone | null {
  return state === 'completed' || state === 'error' ? state : null
}

export interface DesktopPetPresentation {
  state: DesktopPetVisualState
  eyebrow: string
  title: string
  detail: string
  actionLabel: string
  actionRoute: string
  inlineNotice?: string
  progressPercent?: number
  operation?: OperationRun
}

const ACTIVE_STATUSES = new Set<OperationRun['status']>([
  'queued',
  'running',
  'waiting_user',
  'paused',
])

function operationTimestamp(operation: OperationRun) {
  const timestamp = apiDateTimeMs(
    operation.updated_at || operation.last_activity_at || operation.created_at,
  )
  return Number.isFinite(timestamp) ? timestamp : 0
}

function operationPriority(operation: OperationRun) {
  if (
    operation.status === 'failed'
    || operation.health_status === 'stalled'
    || operation.health_status === 'disconnected'
  ) return 0
  if (operation.status === 'waiting_user') return 1
  if (operation.status === 'running') return 2
  if (operation.status === 'queued') return 3
  if (operation.status === 'paused') return 4
  return 5
}

function isRecentTerminal(operation: OperationRun, now: number) {
  const age = Math.max(0, now - operationTimestamp(operation))
  if (operation.status === 'failed') return age <= 120_000
  if (operation.status === 'completed') return age <= 30_000
  return false
}

export function selectDesktopPetOperation(
  operations: OperationRun[],
  now = Date.now(),
) {
  const active = operations
    .filter((operation) => (
      ACTIVE_STATUSES.has(operation.status)
      || operation.health_status === 'stalled'
      || operation.health_status === 'disconnected'
    ))
    .sort((left, right) => (
      operationPriority(left) - operationPriority(right)
      || operationTimestamp(right) - operationTimestamp(left)
    ))

  if (active[0]) return active[0]

  return operations
    .filter((operation) => isRecentTerminal(operation, now))
    .sort((left, right) => operationTimestamp(right) - operationTimestamp(left))[0]
}

function operationRoute(operation?: OperationRun) {
  return operation?.attention?.action_url || operation?.resume_url || '/gui'
}

function operationDetail(operation: OperationRun, fallback: string) {
  return operation.attention?.message
    || operation.current_message
    || operation.result_summary
    || operation.result?.summary
    || fallback
}

export function deriveDesktopPetPresentation(
  operations: OperationRun[],
  now = Date.now(),
): DesktopPetPresentation {
  const operation = selectDesktopPetOperation(operations, now)
  if (!operation) {
    return {
      state: 'idle',
      eyebrow: '陪写模式',
      title: '我在呀～',
      detail: '小书抱好啦，我陪你慢慢写完这一页。',
      actionLabel: '一起写',
      actionRoute: '/gui',
    }
  }

  const common = {
    actionLabel: '查看任务',
    actionRoute: operationRoute(operation),
    operation,
    progressPercent: operation.progress?.mode === 'determinate'
      ? operation.progress.percent ?? undefined
      : undefined,
  }

  if (
    operation.status === 'failed'
    || operation.health_status === 'stalled'
    || operation.health_status === 'disconnected'
  ) {
    const detail = operationDetail(operation, '这页打了个小结，帮我看看嘛。')
    return {
      ...common,
      state: 'error',
      eyebrow: '咦，卡住啦',
      title: '书页打结了……',
      detail,
      inlineNotice: detail,
    }
  }

  if (operation.status === 'queued') {
    return {
      ...common,
      state: 'thinking',
      eyebrow: '翻翻小本本',
      title: '让我想想……',
      detail: operationDetail(operation, '排好队啦，马上就轮到这一页。'),
    }
  }

  if (operation.status === 'running') {
    return {
      ...common,
      state: 'writing',
      eyebrow: '认真写字中',
      title: '沙沙沙……',
      detail: operationDetail(operation, '正在努力写，墨迹还是热乎的呢。'),
    }
  }

  if (operation.status === 'waiting_user' || operation.status === 'paused') {
    return {
      ...common,
      state: 'waiting_author',
      eyebrow: operation.status === 'paused' ? '先压住书页' : '等你点头',
      title: operation.status === 'paused'
        ? '我先停一下'
        : operation.attention?.title || '稿稿等你呀',
      detail: operationDetail(
        operation,
        operation.status === 'paused'
          ? '书签夹好啦，想继续时再叫我。'
          : '草稿抱好啦，就等你说可以。',
      ),
      inlineNotice: operation.status === 'waiting_user'
        ? '稿稿等你点头啦'
        : '书签夹好，等你回来',
    }
  }

  if (operation.status === 'completed') {
    return {
      ...common,
      state: 'completed',
      eyebrow: '小书页完成',
      title: '锵锵，写好啦！',
      detail: operationDetail(operation, '这页写好啦，快来看看合不合心意。'),
    }
  }

  return {
    state: 'idle',
    eyebrow: '陪写模式',
    title: '我在呀～',
    detail: operationDetail(operation, '小书抱好啦，随时可以继续写。'),
    actionLabel: '一起写',
    actionRoute: operationRoute(operation),
    operation,
  }
}
