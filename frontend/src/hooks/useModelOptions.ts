import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiClient } from '../api/client'

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface ModelConfig {
  id: string
  provider: string
  default_model: string
  is_global_default: boolean
  readiness_status: 'detected' | 'unverified' | 'testing' | 'ready' | 'auth_required' | 'quota_limited' | 'unavailable'
  is_usable: boolean
  readiness_message?: string
  readiness_source?: string | null
  failure_class?: string | null
  last_tested_at?: string | null
  base_url_override?: string
  max_output_tokens?: number | null
  effective_max_output_tokens?: number
  deconstruct_input_char_limit?: number | null
  effective_deconstruct_input_char_limit?: number
  deconstruct_item_char_limit?: number | null
  effective_deconstruct_item_char_limit?: number
  created_at?: string
  updated_at?: string
}

export interface ModelSelectOption {
  value: string
  label: string
  provider: string
  model: string
  isGlobalDefault: boolean
}

const PROVIDER_LABEL_MAP: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic Claude',
  deepseek: 'DeepSeek',
  qwen: '通义千问',
  gemini: 'Google Gemini',
  claude_cli: 'Claude Code CLI',
  codex_cli: 'Codex CLI',
  opencode_cli: 'opencode CLI',
  mimocode_cli: 'MiMo Code CLI',
  cursor_cli: 'Cursor Agent CLI',
  kilocode_cli: 'Kilo Code CLI',
  qwen_code_cli: 'Qwen Code CLI',
  hermes_cli: 'Hermes Agent CLI',
  openclaw_cli: 'OpenClaw CLI',
  custom_cli: '自定义本机 CLI',
  local_llama_cpp: '司命本地 AI',
}

const modelValue = (provider: string, model: string) => (
  model.includes(':') ? model : `${provider}:${model}`
)

const normalizeModel = (provider: string, model: string) => {
  if (provider === 'deepseek' && model === 'deepseek-v3') {
    return 'deepseek-v4-flash'
  }
  if (provider === 'gemini' && model.startsWith('models/')) {
    return model.slice('models/'.length)
  }
  return model
}

export function useModelOptions() {
  const [configs, setConfigs] = useState<ModelConfig[]>([])
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<{ items: ModelConfig[]; total: number }>>('/config/models')
      setConfigs(res.data.data.items || [])
    } catch {
      setConfigs([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const modelOptions = useMemo<ModelSelectOption[]>(() => (
    configs.filter((config) => config.is_usable && config.readiness_status === 'ready').map((config) => {
      const model = normalizeModel(config.provider, config.default_model)
      const localRuntimeSuffix = config.provider === 'local_llama_cpp' ? '（本地文本）' : ''
      return {
        value: modelValue(config.provider, model),
        label: `${PROVIDER_LABEL_MAP[config.provider] || config.provider} · ${model}${localRuntimeSuffix}${config.is_global_default ? '（全局默认）' : ''}`,
        provider: config.provider,
        model,
        isGlobalDefault: config.is_global_default,
      }
    })
  ), [configs])

  const defaultModel = useMemo(
    () => modelOptions.find((option) => option.isGlobalDefault)?.value,
    [modelOptions],
  )

  const detectedConfigs = useMemo(
    () => configs.filter((config) => !config.is_usable),
    [configs],
  )

  return {
    configs,
    modelOptions,
    defaultModel,
    loading,
    refresh,
    hasModels: modelOptions.length > 0,
    hasDetectedModels: detectedConfigs.length > 0,
    detectedConfigs,
  }
}
