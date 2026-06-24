export interface HardwareProfile {
  os: string
  arch: string
  cpu_count: number
  ram_gb: number
  gpu_name?: string | null
  vram_gb: number
  nvidia_available: boolean
  profile: 'light' | 'standard' | 'quality'
  recommended_model: string
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

export interface CatalogResponse {
  items: LocalModel[]
  runtime: RuntimeStatus
  model_root: string
  task_settings: Record<string, {
    model_key: string
    adapter_ids: string[]
    context_length?: number | null
    allow_api_fallback: boolean
  }>
}
