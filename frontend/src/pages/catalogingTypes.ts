export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface ChapterItem {
  id: string
  title: string
  word_count: number
}

export type CatalogingMode = 'auto' | 'manual' | 'external_agent'

export interface CatalogingJob {
  id: string
  project_id: string
  status: string
  execution_mode: CatalogingMode
  execution_backend?: 'internal_llm' | 'local_cli_agent' | 'external_agent' | string
  agent_run_id?: string | null
  current_chapter_id?: string | null
  last_completed_chapter_id?: string | null
  blocked_chapter_id?: string | null
  context_integrity?: string | null
  model?: string | null
  effective_model?: string | null
  model_source?: string | null
  provider?: string | null
  total_chapters: number
  completed_chapters: number
  failed_chapters: number
  error?: string | null
  created_at?: string | null
  updated_at?: string | null
  completed_at?: string | null
}

export interface CatalogingRun {
  id: string
  chapter_id: string
  chapter_title: string
  status: string
  chapter_order: number
  error?: string | null
}

export interface CatalogingCandidate {
  id: string
  chapter_id: string
  item_type: string
  chapter_run_id: string
  target_name?: string | null
  payload: Record<string, unknown>
  status: string
  confidence?: number | null
  evidence?: string | null
  error?: string | null
}

export interface CatalogingFact {
  id: string
  job_id?: string
  chapter_run_id: string
  chapter_id: string
  fact_type: string
  payload: Record<string, unknown>
  confidence?: number | null
  evidence?: string | null
  sort_order?: number | null
  status?: string | null
  error?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface CharacterSnapshot {
  id: string
  name: string
  aliases?: string[]
  appearance?: string | null
  personality?: string | null
  background?: string | null
  abilities?: string[]
  role_type?: string | null
  life_status?: string | null
  current_location?: string | null
  realm_or_level?: string | null
  physical_state?: string | null
  mental_state?: string | null
  current_goal?: string | null
  active_conflict?: string | null
  abilities_state?: string | null
  items_or_assets?: string | null
}

export interface CharacterMergePreview {
  candidate: CatalogingCandidate
  payload: Record<string, unknown>
  primary?: CharacterSnapshot | null
  secondary?: CharacterSnapshot | null
}

export const catalogingStatusColor: Record<string, string> = {
  queued: 'default',
  running: 'processing',
  waiting_confirmation: 'orange',
  paused_on_failure: 'red',
  paused: 'orange',
  completed: 'green',
  failed: 'red',
  pending: 'default',
  edited: 'blue',
  approved: 'green',
  rejected: 'red',
  applying: 'processing',
  applied: 'green',
  apply_failed: 'red',
}

export const catalogingStatusLabel: Record<string, string> = {
  queued: '等待开始',
  running: '正在建档',
  waiting_confirmation: '等待确认',
  paused_on_failure: '遇到问题，已暂停',
  paused: '已暂停',
  completed: '建档完成',
  failed: '建档失败',
  cancelled: '已取消',
  pending: '待处理',
  edited: '已编辑',
  approved: '已确认',
  rejected: '已拒绝',
  applying: '正在写入',
  applied: '已写入',
  apply_failed: '写入失败',
}

export function safeStringify(value: unknown) {
  return JSON.stringify(value || {}, null, 2)
}
