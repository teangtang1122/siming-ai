import { useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../api/client'

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface SharedModelConfig {
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
  api_protocol?: 'auto' | 'chat_completions' | 'responses'
  provider_type?: string
  cli_command?: string | null
  cli_args?: string | null
  api_key_masked?: string
  api_key_configured?: boolean
  max_output_tokens?: number | null
  effective_max_output_tokens?: number
  context_window_tokens?: number | null
  context_safety_margin_tokens?: number
  context_profile_source?: string | null
  context_profile_known?: boolean
  deconstruct_input_char_limit?: number | null
  effective_deconstruct_input_char_limit?: number
  deconstruct_item_char_limit?: number | null
  effective_deconstruct_item_char_limit?: number
  available_models?: Array<{
    id: string
    display_name?: string | null
    context_window_tokens?: number
    max_output_tokens?: number
    safety_margin_tokens?: number
    capacity_source?: string
  }>
  created_at?: string
  updated_at?: string
}

export type ModelTaskType = 'assistant' | 'planning' | 'cataloging' | 'writing' | 'evaluation' | 'deconstruct'

export interface SharedTaskModelSetting {
  task_type: ModelTaskType
  provider: string
  model: string
  context_length?: number | null
  is_usable?: boolean
}

interface ModelConfigList {
  items: SharedModelConfig[]
  total?: number
  task_models: Partial<Record<ModelTaskType, SharedTaskModelSetting>>
}

interface GlobalModelSelection {
  provider: string
  model: string
}

export const modelConfigKeys = {
  all: ['model-configs'] as const,
}

async function fetchModelConfigs(): Promise<ModelConfigList> {
  const response = await apiClient.get<ApiResponse<ModelConfigList>>('/config/models')
  return {
    items: response.data.data.items || [],
    total: response.data.data.total,
    task_models: response.data.data.task_models || {},
  }
}

export function useSharedModelConfigs() {
  return useQuery({
    queryKey: modelConfigKeys.all,
    queryFn: fetchModelConfigs,
    // Keep the authoritative selection in cache after a successful update;
    // an immediate stale refetch would briefly restore the previous default.
    staleTime: 30_000,
  })
}

export function useGlobalModelActions() {
  const queryClient = useQueryClient()

  const setGlobalModel = useCallback(async (provider: string, model: string) => {
    const response = await apiClient.put<ApiResponse<GlobalModelSelection>>('/config/global-model', {
      provider,
      model,
    })
    const payload = response.data?.data
    // Older gateways may acknowledge the update without echoing the
    // selection. Keep the requested value authoritative after a successful PUT.
    const saved = {
      provider: payload?.provider || provider,
      model: payload?.model || model,
    }

    // Publish only after the server accepts the change. A failed request leaves
    // every mounted consumer on the previous shared-cache value.
    queryClient.setQueryData<ModelConfigList>(modelConfigKeys.all, (current) => {
      if (!current) return current
      return {
        ...current,
        items: current.items.map((config) => (
          config.provider === saved.provider
            ? { ...config, default_model: saved.model, is_global_default: true }
            : { ...config, is_global_default: false }
        )),
      }
    })
    if (!queryClient.getQueryData(modelConfigKeys.all)) {
      await queryClient.invalidateQueries({ queryKey: modelConfigKeys.all })
    }
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('siming:global-model-changed', {
        detail: `${saved.provider}:${saved.model}`,
      }))
    }
    return saved
  }, [queryClient])

  return { setGlobalModel }
}

export function useTaskModelActions() {
  const queryClient = useQueryClient()

  const setTaskModel = useCallback(async (
    taskType: ModelTaskType,
    provider: string,
    model: string,
    contextLength?: number | null,
  ) => {
    const response = await apiClient.put<ApiResponse<SharedTaskModelSetting>>(
      `/config/task-models/${taskType}`,
      {
        provider,
        model,
        context_length: contextLength || null,
      },
    )
    const saved = response.data?.data || {
      task_type: taskType,
      provider,
      model,
      context_length: contextLength || null,
      is_usable: true,
    }
    queryClient.setQueryData<ModelConfigList>(modelConfigKeys.all, (current) => (
      current
        ? {
            ...current,
            task_models: { ...current.task_models, [taskType]: saved },
          }
        : current
    ))
    if (!queryClient.getQueryData(modelConfigKeys.all)) {
      await queryClient.invalidateQueries({ queryKey: modelConfigKeys.all })
    }
    return saved
  }, [queryClient])

  const clearTaskModel = useCallback(async (taskType: ModelTaskType) => {
    await apiClient.delete(`/config/task-models/${taskType}`)
    queryClient.setQueryData<ModelConfigList>(modelConfigKeys.all, (current) => {
      if (!current) return current
      const taskModels = { ...current.task_models }
      delete taskModels[taskType]
      return { ...current, task_models: taskModels }
    })
  }, [queryClient])

  return { setTaskModel, clearTaskModel }
}
