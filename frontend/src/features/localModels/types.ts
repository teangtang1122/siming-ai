export interface HardwareProfile {
  os: string
  arch: string
  cpu_count: number
  ram_gb: number
  gpu_name?: string | null
  vram_gb: number
  nvidia_available: boolean
  profile: 'unsupported' | 'standard' | 'quality'
  recommended_model: string | null
  recommended_context: number
  training_supported: boolean
}

export interface LocalModel {
  id: string
  model_key: string
  display_name: string
  family: string
  parameter_size?: string
  quantization?: string
  context_length: number
  file_path?: string | null
  file_size?: number | null
  sha256?: string | null
  license_name?: string
  source?: 'catalog' | 'custom'
  source_urls: string[]
  min_ram_gb?: number
  recommended_vram_gb?: number
  status: string
}

export interface DownloadTask {
  id: string
  kind: string
  target_key: string
  status: string
  downloaded_bytes: number
  total_bytes?: number | null
  error_message?: string | null
}

export interface RuntimeStatus {
  status: string
  version?: string | null
  backend?: string | null
  running: boolean
  model_key?: string | null
  pid?: number | null
  context_length?: number | null
  requested_context_length?: number | null
  executable_path?: string | null
  adjustment?: string | null
}

export interface RuntimeLaunchSettings {
  context_length: number
  gpu_layers: number
  threads: number | null
  flash_attention: 'auto' | 'on' | 'off'
  fit: 'auto' | 'on' | 'off'
  kv_cache_type: 'f16' | 'q8_0' | 'q4_0'
  mtp_draft_tokens: number
  cache_ram_mb: number
  reasoning_effort: 'low' | 'medium' | 'high' | null
  temperature: number | null
  top_p: number | null
  top_k: number | null
  min_p: number | null
  repeat_penalty: number | null
}

export interface ModelAdapter {
  id: string
  project_id?: string | null
  base_model_key: string
  name: string
  scope: string
  weight: number
  enabled: boolean
  is_default_for_writing: boolean
  metrics: Record<string, unknown>
}

export interface TrainingDataset {
  id: string
  project_id?: string | null
  name: string
  sample_count: number
  train_count: number
  eval_count: number
  rights_confirmed: boolean
  stats: Record<string, number>
}

export interface TrainingJob {
  id: string
  project_id?: string | null
  dataset_id?: string | null
  base_model_key: string
  name: string
  status: string
  progress: number
  current_step: number
  total_steps?: number | null
  metrics: Record<string, unknown>
  output_path?: string | null
  error_message?: string | null
  log_tail?: string
}

export interface LocalModelQualificationCase {
  id: string
  label: string
  passed: boolean
  detail: string
  elapsed_seconds: number
  output_preview: string
  input_characters: number
}

export interface LocalModelQualification {
  version: string
  model_key: string
  context_length: number
  rating: 'qualified' | 'limited' | 'failed'
  passed: boolean
  passed_count: number
  total_count: number
  elapsed_seconds: number
  cases: LocalModelQualificationCase[]
}

export interface CatalogResponse {
  usage_enabled?: boolean
  usage_disabled_reason?: string | null
  items: LocalModel[]
  runtime: RuntimeStatus
  model_root: string
}
