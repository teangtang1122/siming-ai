import { formatApiDateTime } from '../../utils/dateTime'
import { useState, useCallback, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  Card,
  Collapse,
  Typography,
  Table,
  Button,
  Modal,
  Form,
  Input,
  AutoComplete,
  Select,
  Tag,
  message,
  Space,
  Divider,
  Descriptions,
  InputNumber,
  Radio,
  Alert,
  Tabs,
} from 'antd'
import {
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ReloadOutlined,
  FolderOpenOutlined,
  SaveOutlined,
  DesktopOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  ApiOutlined,
  RollbackOutlined,
} from '@ant-design/icons'
import { apiClient } from '../../api/client'
import { projectKeys } from '../projects'
import SystemNav from '../../components/SystemNav'
import ContextGovernanceSettingsPanel from '../../components/ContextGovernanceSettingsPanel'
import ModelReadinessBanner from '../../components/ModelReadinessBanner'
import {
  type ModelTaskType,
  type SharedModelConfig,
  useGlobalModelActions,
  useSharedModelConfigs,
  useTaskModelActions,
} from '../../shared/query/modelConfigs'
import { useModelOptions } from '../../hooks/useModelOptions'
import GatewaySettingsPanel from '../gateway/GatewaySettingsPanel'
import type { LauncherGatewaySettings } from '../gateway/types'
import {
  UpdateSettingsCard,
  type UpdateChannel,
  type UpdateDownloadSourceKey,
  type UpdateStatus,
} from './UpdateSettingsCard'
import { useInitialSettingsLoad } from './useInitialSettingsLoad'
import {
  CUSTOM_PROVIDER_VALUE,
  DEFAULT_CLI_ARGS,
  DEFAULT_CLI_COMMANDS,
  PROVIDER_ID_PATTERN,
  PROVIDER_OPTIONS,
  READINESS_LABELS,
  defaultOutputLimit,
  defaultSafetyLimits,
  fallbackModelOptions,
  isCustomProviderSelection,
  isDeepSeekModelSupported,
  isKnownProvider,
  isLocalCliProvider,
  modelCapacityDefaults,
  normalizeDefaultModel,
  normalizeProviderModelOptions,
  providerColor,
  providerLabel,
  readinessColor,
  resolveProviderForSubmit,
  type ModelDiscoveryState,
  type ModelOption,
} from '../localModels/settingsModelOptions'

const { Title, Paragraph, Text } = Typography

const TASK_MODEL_CHOICES: Array<{
  value: ModelTaskType
  label: string
  description: string
}> = [
  { value: 'assistant', label: '项目助手', description: '对话、工具选择与项目协作' },
  { value: 'planning', label: '立项与规划', description: '新书立项、大纲、角色和世界观' },
  { value: 'cataloging', label: '作品建档', description: '章节事实、角色与设定抽取' },
  { value: 'writing', label: '章节写作', description: '正文生成、改写、扩写与续写' },
  { value: 'evaluation', label: '质量评估', description: '章节评分、冲突检测与审阅' },
  { value: 'deconstruct', label: '拆书分析', description: '分块阅读与最终合并分析' },
]

type ModelConfig = SharedModelConfig

const persistedModelOptions = (config?: ModelConfig): ModelOption[] => {
  if (!config) return []
  const defaultModel = normalizeDefaultModel(config.provider, config.default_model)
  const otherModels = (config.available_models || [])
    .filter((option) => normalizeDefaultModel(config.provider, option.id) !== defaultModel)
    .map((option) => ({
      ...option,
      display_name: option.display_name || option.id,
    }))
  return normalizeProviderModelOptions(
    config.provider,
    [
      {
        id: defaultModel,
        display_name: defaultModel,
        context_window_tokens: config.context_window_tokens || undefined,
        safety_margin_tokens: config.context_safety_margin_tokens,
        capacity_source: config.context_profile_source || undefined,
      },
      ...otherModels,
    ],
  )
}

interface ContentRootSettings {
  current_path: string
  configured_path?: string | null
  default_path: string
  is_default: boolean
  exists: boolean
  is_empty: boolean
  looks_like_siming_root?: boolean
  looks_like_moshu_root?: boolean
  cancelled?: boolean
  migration?: {
    previous_root?: string
    target_root?: string
    migrated_projects?: number
    cleaned_project_folders?: number
  }
}

type LaunchMode = 'desktop' | 'browser'

interface LauncherSettings extends LauncherGatewaySettings {
  launch_mode: LaunchMode
  update_channel: UpdateChannel
  restart_required: boolean
  browser_mode_description: string
}

interface CliIntegration {
  provider: string
  label: string
  detected: boolean
  command?: string | null
  config_path?: string | null
  configured: boolean
  can_restore: boolean
  changed?: boolean
  status?: string
  detail?: string
}

interface CliIntegrationScan {
  status: string
  clients: CliIntegration[]
  detected_count: number
  supported_count: number
}

interface SettingsPageProps {
  embedded?: boolean
}

function SettingsPage({ embedded = false }: SettingsPageProps = {}) {
  const contextGovernanceRequested = typeof window !== 'undefined'
    && new URLSearchParams(window.location.search).get('section') === 'context-governance'
  const queryClient = useQueryClient()
  const modelConfigsQuery = useSharedModelConfigs()
  const { setGlobalModel: persistGlobalModel } = useGlobalModelActions()
  const { setTaskModel, clearTaskModel } = useTaskModelActions()
  const { modelOptions: availableTaskModelOptions } = useModelOptions()
  const configs = modelConfigsQuery.data?.items || []
  const loading = modelConfigsQuery.isLoading || modelConfigsQuery.isFetching
  const globalConfig = configs.find((config) => config.is_global_default && config.is_usable)
  const globalModel = {
    provider: globalConfig?.provider || null,
    model: globalConfig?.default_model || null,
  }
  const [modalOpen, setModalOpen] = useState(false)
  const [editingProvider, setEditingProvider] = useState<string | null>(null)
  const [form] = Form.useForm()
  const modalProvider = Form.useWatch('provider', form)
  const contextProfileSource = Form.useWatch('context_profile_source', form)

  const [modelOptions, setModelOptions] = useState<ModelOption[]>([])
  const [modelDiscovery, setModelDiscovery] = useState<ModelDiscoveryState>({ status: 'idle' })
  const [modelsLoading, setModelsLoading] = useState(false)
  const [testingConnection, setTestingConnection] = useState(false)
  const [verifyingProvider, setVerifyingProvider] = useState<string>()
  const [connectionTestResult, setConnectionTestResult] = useState<{ success: boolean; message: string } | null>(null)
  const [contentRoot, setContentRoot] = useState<ContentRootSettings | null>(null)
  const [contentRootPath, setContentRootPath] = useState('')
  const [contentRootLoading, setContentRootLoading] = useState(false)
  const [launcherSettings, setLauncherSettings] = useState<LauncherSettings | null>(null)
  const [launchMode, setLaunchMode] = useState<LaunchMode>('desktop')
  const [updateChannel, setUpdateChannel] = useState<UpdateChannel>('stable')
  const [launcherLoading, setLauncherLoading] = useState(false)
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus | null>(null)
  const [checkingUpdate, setCheckingUpdate] = useState(false)
  const [downloadingUpdate, setDownloadingUpdate] = useState(false)
  const [installingUpdate, setInstallingUpdate] = useState(false)
  const [settingsSection, setSettingsSection] = useState<'ai' | 'app' | 'gateway'>('ai')
  const [pendingModelsOpen, setPendingModelsOpen] = useState(false)
  const [cliIntegrations, setCliIntegrations] = useState<CliIntegrationScan | null>(null)
  const [cliScanLoading, setCliScanLoading] = useState(false)
  const [cliAction, setCliAction] = useState<string | null>(null)
  const [savingTaskModel, setSavingTaskModel] = useState<ModelTaskType>()

  const optionForModel = (
    provider: string | undefined,
    model: string | undefined,
    options: ModelOption[] = modelOptions,
  ) => options.find((option) => (
    normalizeDefaultModel(provider || '', option.id) === normalizeDefaultModel(provider || '', model || '')
  ))

  const applyCapacityForModel = (
    provider: string | undefined,
    model: string | undefined,
    options: ModelOption[] = modelOptions,
    baseUrlOverride: string | undefined = form.getFieldValue('base_url_override'),
  ) => {
    form.setFieldsValue(modelCapacityDefaults(
      provider,
      model,
      optionForModel(provider, model, options),
      baseUrlOverride,
    ))
  }

  const fetchConfigs = useCallback(async () => {
    const result = await modelConfigsQuery.refetch()
    if (result.error) {
      message.error(result.error instanceof Error ? result.error.message : '获取模型配置失败')
    }
  }, [modelConfigsQuery])

  const fetchContentRoot = useCallback(async () => {
    setContentRootLoading(true)
    try {
      const res = await apiClient.get<{ code: number; data: ContentRootSettings }>('/config/content-root')
      setContentRoot(res.data.data)
      setContentRootPath(res.data.data.current_path || res.data.data.default_path || '')
    } catch (err: any) {
      message.error(err.message || '获取小说数据目录失败')
    } finally {
      setContentRootLoading(false)
    }
  }, [])

  const fetchLauncherSettings = useCallback(async () => {
    setLauncherLoading(true)
    try {
      const res = await apiClient.get<{ code: number; data: LauncherSettings }>('/config/launcher')
      setLauncherSettings(res.data.data)
      setLaunchMode(res.data.data.launch_mode)
      setUpdateChannel(res.data.data.update_channel || 'stable')
      if (res.data.data.gateway_headless && !embedded && !contextGovernanceRequested) setSettingsSection('gateway')
    } catch (err: any) {
      message.error(err.message || '获取启动方式失败')
    } finally {
      setLauncherLoading(false)
    }
  }, [contextGovernanceRequested, embedded])

  useEffect(() => {
    if (!contextGovernanceRequested) return
    setSettingsSection('ai')
    const frame = window.requestAnimationFrame(() => {
      document.getElementById('context-governance-settings')?.scrollIntoView?.({ block: 'start' })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [contextGovernanceRequested])

  const scanCliIntegrations = async () => {
    setCliScanLoading(true)
    try {
      const res = await apiClient.post<{ code: number; data: CliIntegrationScan }>('/config/cli-integrations/scan')
      setCliIntegrations(res.data.data)
      if (res.data.data.detected_count) message.success(`已找到 ${res.data.data.detected_count} 个本机 CLI；尚未修改任何配置`)
      else message.info('没有找到受支持的本机 CLI；尚未修改任何配置')
    } catch (err: any) {
      message.error(err.message || '扫描本机 CLI 失败')
    } finally {
      setCliScanLoading(false)
    }
  }

  const configureCliIntegration = (client: CliIntegration) => {
    Modal.confirm({
      title: `允许司命自动配置 ${client.label}？`,
      width: 560,
      icon: <SafetyCertificateOutlined />,
      content: (
        <div className="settings-cli-consent">
          <Paragraph>只有确认后，司命才会修改这个 CLI 的配置；其他 CLI 不受影响。</Paragraph>
          <ul>
            <li>写入名为 siming 的 MCP 服务，并保留其他服务配置。</li>
            <li>不会改变该 CLI 现有的沙箱、审批或工具权限设置。</li>
            <li>修改前会保存可还原快照；若文件之后又被修改，还原会停止，避免覆盖新内容。</li>
          </ul>
          {client.config_path && <Text type="secondary">当前配置：<Text code>{client.config_path}</Text></Text>}
        </div>
      ),
      okText: '我知情，自动配置',
      cancelText: '取消',
      onOk: async () => {
        setCliAction(`configure:${client.provider}`)
        try {
          const res = await apiClient.post<{ code: number; data: CliIntegration }>(`/config/cli-integrations/${client.provider}/configure`)
          const result = res.data.data
          if (result.status === 'configured' && result.changed) message.success(`${client.label} 已配置，可随时在这里还原`)
          else if (result.status === 'configured') message.info(`${client.label} 已是所需配置；未修改任何文件`)
          else message.warning(result.detail || `${client.label} 未发生修改`)
          await scanCliIntegrations()
        } catch (err: any) {
          message.error(err.message || `${client.label} 配置失败`)
        } finally {
          setCliAction(null)
        }
      },
    })
  }

  const restoreCliIntegration = (client: CliIntegration) => {
    Modal.confirm({
      title: `还原 ${client.label} 的司命配置？`,
      content: '司命会先确认配置文件仍与自动配置完成时一致；如果你或其他程序后来改过文件，将停止还原且不会覆盖新内容。',
      okText: '检查并还原',
      cancelText: '取消',
      onOk: async () => {
        setCliAction(`restore:${client.provider}`)
        try {
          const res = await apiClient.post<{ code: number; data: CliIntegration }>(`/config/cli-integrations/${client.provider}/restore`)
          const result = res.data.data
          if (result.status === 'restored') message.success(`${client.label} 已还原`)
          else if (result.status === 'conflict') message.warning(result.detail)
          else message.info(result.detail || '没有需要还原的配置')
          await scanCliIntegrations()
        } catch (err: any) {
          message.error(err.message || `${client.label} 还原失败`)
        } finally {
          setCliAction(null)
        }
      },
    })
  }

  useInitialSettingsLoad(fetchContentRoot, fetchLauncherSettings)

  const saveLaunchMode = async () => {
    setLauncherLoading(true)
    try {
      const res = await apiClient.put<{ code: number; data: LauncherSettings }>('/config/launcher', {
        launch_mode: launchMode,
      })
      setLauncherSettings(res.data.data)
      message.success('启动方式已保存，下次启动生效')
    } catch (err: any) {
      message.error(err.message || '保存启动方式失败')
    } finally {
      setLauncherLoading(false)
    }
  }

  const saveUpdateChannel = async () => {
    setLauncherLoading(true)
    try {
      const res = await apiClient.put<{ code: number; data: LauncherSettings }>('/config/launcher', {
        update_channel: updateChannel,
      })
      setLauncherSettings(res.data.data)
      setUpdateStatus(null)
      message.success('更新通道已保存')
    } catch (err: any) {
      message.error(err.message || '保存更新通道失败')
    } finally {
      setLauncherLoading(false)
    }
  }

  const saveGatewaySettings = async (values: Partial<LauncherGatewaySettings>) => {
    try {
      const res = await apiClient.put<{ code: number; data: LauncherSettings }>('/config/launcher', values)
      setLauncherSettings(res.data.data)
      message.success(values.gateway_enabled === false
        ? 'Gateway 关闭设置已保存，重启后生效'
        : 'Gateway 设置已保存，重启后生效')
    } catch (err: any) {
      message.error(err.message || '保存 Gateway 设置失败')
      throw err
    }
  }

  const checkForUpdates = async () => {
    setCheckingUpdate(true)
    try {
      const res = await apiClient.post<{ code: number; data: UpdateStatus }>('/config/update/check')
      setUpdateStatus(res.data.data)
      if (res.data.data.update_available) {
        message.success(`发现司命 ${res.data.data.update?.version || '新'}版本`)
      } else {
        message.info('当前已是最新版本，或暂时无法获取更新信息')
      }
    } catch (err: any) {
      message.error(err.message || '检查更新失败')
    } finally {
      setCheckingUpdate(false)
    }
  }

  const downloadUpdate = (source: UpdateDownloadSourceKey) => {
    if (!updateStatus?.update) return
    const sourceLabel = source === 'gitee'
      ? 'Gitee 国内镜像'
      : source === 'github'
        ? 'GitHub'
        : '配置的更新源'
    const verificationLabel = updateStatus.signature_verification_required
      ? 'SHA256 和 Windows 代码签名'
      : 'SHA256'
    Modal.confirm({
      title: `通过 ${sourceLabel} 下载司命 ${updateStatus.update.version}？`,
      content: `下载后会在本机校验 ${verificationLabel}。校验失败的文件会被删除，不会安装。`,
      okText: '下载并校验',
      cancelText: '取消',
      onOk: async () => {
        setDownloadingUpdate(true)
        try {
          const res = await apiClient.post<{ code: number; data: UpdateStatus }>('/config/update/download', { source })
          setUpdateStatus(res.data.data)
          const downloadedFrom = res.data.data.staged_update?.download_source_label
          message.success(`更新已${downloadedFrom ? `从 ${downloadedFrom} ` : ''}下载并通过 ${verificationLabel} 校验，可以安装`)
        } catch (err: any) {
          message.error(err.message || '更新未通过安全校验，已停止安装')
        } finally {
          setDownloadingUpdate(false)
        }
      },
    })
  }

  const installUpdate = () => {
    if (!updateStatus?.staged_update?.ready_to_install) return
    Modal.confirm({
      title: '安装已验证的更新？',
      content: '司命会关闭当前窗口，由已验证的新版本替换旧程序后重新启动。未验证的更新不会被安装。',
      okText: '安装并重启',
      cancelText: '取消',
      onOk: async () => {
        setInstallingUpdate(true)
        try {
          await apiClient.post('/config/update/install')
          message.success('更新已安排，司命即将重启')
        } catch (err: any) {
          setInstallingUpdate(false)
          message.error(err.message || '安装更新失败')
        }
      },
    })
  }

  const applyContentRootResponse = (settings: ContentRootSettings, successText: string) => {
    setContentRoot(settings)
    setContentRootPath(settings.current_path || settings.default_path || '')
    const migrated = settings.migration?.migrated_projects
    if (typeof migrated === 'number') {
      message.success(`${successText}，已迁移 ${migrated} 个作品`)
    } else {
      message.success(successText)
    }
    void queryClient.invalidateQueries({ queryKey: projectKeys.all })
  }

  const saveContentRoot = async () => {
    const path = contentRootPath.trim()
    if (!path) {
      message.warning('请填写小说数据目录')
      return
    }
    Modal.confirm({
      title: '切换小说数据目录',
      content: '新目录必须为空，或已经是司命小说数据目录。保存后会把现有作品资料迁移到新目录。',
      okText: '保存并迁移',
      onOk: async () => {
        setContentRootLoading(true)
        try {
          const res = await apiClient.put<{ code: number; data: ContentRootSettings }>('/config/content-root', { path })
          applyContentRootResponse(res.data.data, '小说数据目录已更新')
        } catch (err: any) {
          message.error(err.message || '更新小说数据目录失败')
        } finally {
          setContentRootLoading(false)
        }
      },
    })
  }

  const pickContentRoot = async () => {
    setContentRootLoading(true)
    try {
      const res = await apiClient.post<{ code: number; data: ContentRootSettings }>('/config/content-root/pick')
      if (res.data.data.cancelled) {
        setContentRoot(res.data.data)
        setContentRootPath(res.data.data.current_path || res.data.data.default_path || '')
        message.info('已取消选择')
        return
      }
      applyContentRootResponse(res.data.data, '小说数据目录已更新')
    } catch (err: any) {
      message.error(err.message || '选择小说数据目录失败')
    } finally {
      setContentRootLoading(false)
    }
  }

  const handleAddOrEdit = (provider?: string) => {
    setConnectionTestResult(null)
    setModelDiscovery({ status: 'idle' })
    if (provider) {
      const cfg = configs.find((c) => c.provider === provider)
      if (cfg) {
        setEditingProvider(provider)
        const defaultModel = normalizeDefaultModel(cfg.provider, cfg.default_model)
        const knownProvider = isKnownProvider(cfg.provider)
        const savedOptions = persistedModelOptions(cfg)
        setModelOptions(savedOptions.length > 0
          ? savedOptions
          : knownProvider
            ? fallbackModelOptions(cfg.provider)
            : [{ id: defaultModel, display_name: defaultModel }])
        if (!knownProvider) {
          setModelDiscovery({ status: 'success', message: '已保留当前模型；可直接使用已保存密钥刷新模型列表。' })
        }
        form.setFieldsValue({
          provider: knownProvider ? cfg.provider : CUSTOM_PROVIDER_VALUE,
          custom_provider: knownProvider ? undefined : cfg.provider,
          default_model: defaultModel,
          base_url_override: cfg.base_url_override || '',
          api_protocol: cfg.api_protocol || 'auto',
          provider_type: cfg.provider_type || (isLocalCliProvider(cfg.provider) ? 'local_cli' : 'api'),
          cli_command: cfg.cli_command || DEFAULT_CLI_COMMANDS[cfg.provider] || '',
          cli_args: cfg.cli_args || DEFAULT_CLI_ARGS[cfg.provider] || '',
          api_key: '',
          max_output_tokens: cfg.max_output_tokens || cfg.effective_max_output_tokens || defaultOutputLimit(cfg.provider, defaultModel),
          context_window_tokens: cfg.context_window_tokens || undefined,
          context_safety_margin_tokens: cfg.context_safety_margin_tokens ?? 512,
          context_profile_source: cfg.context_profile_source || undefined,
          deconstruct_input_char_limit: cfg.deconstruct_input_char_limit || cfg.effective_deconstruct_input_char_limit || defaultOutputLimit(cfg.provider, defaultModel),
          deconstruct_item_char_limit: cfg.deconstruct_item_char_limit || cfg.effective_deconstruct_item_char_limit || defaultOutputLimit(cfg.provider, defaultModel),
        })
        void fetchModels(provider)
      }
    } else {
      setEditingProvider(null)
      setModelOptions([])
      setModelDiscovery({ status: 'idle' })
      form.resetFields()
    }
    setModalOpen(true)
  }

  const handleSubmit = async (values: any) => {
    try {
      const provider = resolveProviderForSubmit(values)
      if (!provider) {
        message.error('请填写自定义提供商标识')
        return
      }
      if (!PROVIDER_ID_PATTERN.test(provider)) {
        message.error('提供商标识只能包含字母、数字、下划线和短横线')
        return
      }
      const isCli = isLocalCliProvider(provider)
      if (isCustomProviderSelection(values.provider) && !values.base_url_override) {
        message.error('自定义 OpenAI 兼容提供商必须填写 API 端点')
        return
      }
      if (isCli && provider === 'custom_cli' && !values.cli_command) {
        message.error('请填写本机 CLI 命令')
        return
      }

      const defaultModel = normalizeDefaultModel(provider, values.default_model)
      if (provider === 'deepseek' && !isDeepSeekModelSupported(defaultModel)) {
        message.error('DeepSeek 当前支持 deepseek-v4-pro 或 deepseek-v4-flash，请重新选择')
        return
      }
      await apiClient.post('/config/models', {
        provider,
        api_key: isCli ? undefined : values.api_key,
        default_model: defaultModel,
        base_url_override: isCli ? null : values.base_url_override || null,
        api_protocol: isCli ? 'chat_completions' : values.api_protocol || 'auto',
        provider_type: isCli ? 'local_cli' : 'api',
        cli_command: isCli ? values.cli_command || DEFAULT_CLI_COMMANDS[provider] || null : null,
        cli_args: isCli ? values.cli_args || DEFAULT_CLI_ARGS[provider] || null : null,
        max_output_tokens: values.max_output_tokens || null,
        context_window_tokens: contextProfileSource && contextProfileSource !== 'configured'
          ? null
          : values.context_window_tokens || null,
        context_safety_margin_tokens: values.context_safety_margin_tokens ?? 512,
        deconstruct_input_char_limit: values.deconstruct_input_char_limit || null,
        deconstruct_item_char_limit: values.deconstruct_item_char_limit || null,
        available_models: modelOptions,
      })
      message.success('配置已保存')
      setModalOpen(false)
      form.resetFields()
      fetchConfigs()
    } catch (err: any) {
      message.error(err.message || '保存配置失败')
    }
  }

  const handleDelete = async (provider: string) => {
    Modal.confirm({
      title: '确认删除',
      content: `确定要删除 ${providerLabel(provider)} 的配置吗？`,
      okText: '删除',
      okType: 'danger',
      onOk: async () => {
        try {
          await apiClient.delete(`/config/models/${provider}`)
          message.success('配置已删除')
          fetchConfigs()
        } catch (err: any) {
          message.error(err.message || '删除配置失败')
        }
      },
    })
  }

  const fetchModels = async (providerOverride?: string) => {
    const values = form.getFieldsValue()
    const provider = providerOverride
      ? (isCustomProviderSelection(providerOverride) ? String(values.custom_provider || '').trim() : providerOverride)
      : resolveProviderForSubmit(values)
    const apiKey = form.getFieldValue('api_key')
    if (!provider) return
    const isCli = isLocalCliProvider(provider)
    const savedConfig = configs.find((item) => item.provider === (editingProvider || provider))
    const savedOptions = persistedModelOptions(savedConfig)
    const fallbackOptions = savedOptions.length > 0
      ? savedOptions
      : fallbackModelOptions(provider)
    const isCustom = isCustomProviderSelection(form.getFieldValue('provider'))
    const baseUrl = form.getFieldValue('base_url_override') || undefined
    if (isCli) {
      setModelsLoading(true)
      setModelDiscovery({ status: 'idle', message: provider === 'opencode_cli' ? '正在运行 OpenCode CLI 获取可用模型…' : '正在通过本机 CLI 获取可用模型…' })
      setModelOptions(fallbackOptions)
      try {
        const res = await apiClient.post<{ code: number; data: { models: ModelOption[] } }>(
          '/config/models/list',
          {
            provider,
            cli_command: form.getFieldValue('cli_command') || DEFAULT_CLI_COMMANDS[provider],
            cli_args: form.getFieldValue('cli_args') || DEFAULT_CLI_ARGS[provider],
          }
        )
        const options = normalizeProviderModelOptions(provider, res.data.data.models || [])
        setModelOptions(options)
        if (form.getFieldValue('context_profile_source') !== 'configured') {
          applyCapacityForModel(provider, form.getFieldValue('default_model'), options)
        }
        setModelDiscovery({
          status: 'success',
          message: provider === 'opencode_cli'
            ? `已由司命运行 OpenCode CLI，获取到 ${options.length} 个可用模型。`
            : `已从本机 CLI 获取到 ${options.length} 个模型。`,
        })
      } catch (err: any) {
        setModelOptions(fallbackOptions)
        setModelDiscovery({ status: 'manual', message: `CLI 模型发现失败：${err.message || '命令不可用'}。仍可手动填写模型名。` })
      } finally {
        setModelsLoading(false)
      }
      return
    }
    if (isCustom && !baseUrl) {
      setModelOptions([])
      setModelDiscovery({ status: 'idle', message: '填写 API 端点和 API Key 后，将自动拉取模型列表。' })
      return
    }
    const savedProvider = providerOverride || editingProvider
    const hasSavedApiKey = Boolean(savedProvider && configs.find((item) => item.provider === savedProvider)?.api_key_configured)
    if (!apiKey && !hasSavedApiKey) {
      setModelOptions(fallbackOptions)
      if (isCustom) {
        setModelDiscovery({ status: 'idle', message: '填写 API 端点和 API Key 后，将自动拉取模型列表。' })
      }
      return
    }

    setModelsLoading(true)
    setModelOptions(fallbackOptions)
    if (isCustom) {
      setModelDiscovery({ status: 'idle', message: '正在自动拉取模型列表…' })
    }
    try {
      const res = await apiClient.post<{
        code: number
        data: { models: ModelOption[]; manual_entry_required?: boolean; warning?: string | null }
      }>(
        '/config/models/list',
        {
          provider,
          api_key: apiKey || undefined,
          base_url_override: baseUrl,
        }
      )
      const options = normalizeProviderModelOptions(provider, res.data.data.models || [])
      setModelOptions(options)
      if (form.getFieldValue('context_profile_source') !== 'configured') {
        applyCapacityForModel(provider, form.getFieldValue('default_model'), options)
      }
      if (isCustom) {
        if (res.data.data.manual_entry_required || options.length === 0) {
          setModelDiscovery({
            status: 'manual',
            message: res.data.data.warning || '服务商未返回模型列表，请手动填写支持的模型名。',
          })
        } else {
          setModelDiscovery({ status: 'success', message: `已自动拉取 ${options.length} 个模型，请选择默认模型。` })
        }
      }
    } catch (err: any) {
      setModelOptions(fallbackOptions)
      if (isCustom) {
        setModelDiscovery({
          status: 'manual',
          message: `自动拉取模型失败：${err.message || '服务暂时不可用'}。你仍可手动填写模型名。`,
        })
      }
    } finally {
      setModelsLoading(false)
    }
  }

  const testConnection = async () => {
    const values = form.getFieldsValue()
    const provider = resolveProviderForSubmit(values)
    const isCli = isLocalCliProvider(provider)
    const apiKey = form.getFieldValue('api_key')
    const hasSavedApiKey = Boolean(editingProvider && configs.find((item) => item.provider === editingProvider)?.api_key_configured)
    if (!provider || (!isCli && !apiKey && !hasSavedApiKey)) {
      message.warning('请先选择提供商并输入 API Key')
      return
    }
    const baseUrl = form.getFieldValue('base_url_override') || undefined
    if (!isCli && !isKnownProvider(provider) && !baseUrl) {
      message.warning('自定义 OpenAI 兼容提供商必须填写 API 端点')
      return
    }
    if (!values.default_model) {
      message.warning('请先填写要实际调用的模型名')
      return
    }

    setTestingConnection(true)
    setConnectionTestResult(null)
    try {
      const response = await apiClient.post<{
        code: number
        data: { api_protocol?: 'chat_completions' | 'responses'; base_url?: string }
      }>('/config/models/test', {
        provider,
        api_key: isCli ? undefined : apiKey || undefined,
        base_url_override: isCli ? undefined : baseUrl,
        api_protocol: isCli ? undefined : values.api_protocol || 'auto',
        cli_command: isCli ? values.cli_command || DEFAULT_CLI_COMMANDS[provider] : undefined,
        cli_args: isCli ? values.cli_args || DEFAULT_CLI_ARGS[provider] : undefined,
        model: values.default_model,
      })
      const protocol = response.data.data?.api_protocol
      const protocolLabel = protocol === 'responses' ? 'Responses API' : 'Chat Completions'
      setConnectionTestResult({
        success: true,
        message: isCli
          ? '本机 CLI 基础对话探测成功；长任务仍可能受到临时限流或服务容量影响'
          : `模型基础对话探测成功（${protocolLabel}）；长任务仍可能受到临时限流或服务容量影响`,
      })
    } catch (err: any) {
      setConnectionTestResult({ success: false, message: err.message || '连接失败' })
    } finally {
      setTestingConnection(false)
    }
  }

  const verifySavedConfig = async (provider: string) => {
    setVerifyingProvider(provider)
    try {
      const response = await apiClient.post<{ code: number; message: string; data: { became_global_default: boolean } }>(
        `/config/models/${provider}/verify`,
      )
      message.success(
        response.data.message
        || '模型已通过基础对话探测；长任务仍可能受到临时限流或服务容量影响',
      )
      await fetchConfigs()
    } catch (err: any) {
      message.error(err.message || '真实对话测试失败')
      await fetchConfigs()
    } finally {
      setVerifyingProvider(undefined)
    }
  }

  const handleSetGlobal = async (provider: string) => {
    const config = configs.find((item) => item.provider === provider)
    if (!config) return
    try {
      const model = normalizeDefaultModel(provider, config.default_model)
      await persistGlobalModel(provider, model)
      message.success('全局默认模型已设置')
    } catch (err: any) {
      message.error(err.message || '设置全局默认模型失败')
    }
  }

  const saveTaskModel = async (
    taskType: ModelTaskType,
    value?: string,
    contextLength?: number | null,
  ) => {
    setSavingTaskModel(taskType)
    try {
      if (!value) {
        await clearTaskModel(taskType)
        message.success('任务默认已清除，将跟随全局默认模型')
        return
      }
      const option = availableTaskModelOptions.find((item) => item.value === value)
      if (!option) {
        message.error('所选模型已不在可用列表中，请刷新模型配置')
        return
      }
      await setTaskModel(taskType, option.provider, option.model, contextLength)
      message.success(`${TASK_MODEL_CHOICES.find((item) => item.value === taskType)?.label || taskType}默认模型已保存`)
    } catch (err: any) {
      message.error(err.message || '任务默认模型保存失败')
    } finally {
      setSavingTaskModel(undefined)
    }
  }

  const columns = [
    {
      title: '提供商',
      dataIndex: 'provider',
      key: 'provider',
      render: (v: string) => (
        <Tag color={providerColor(v)}>{providerLabel(v)}</Tag>
      ),
    },
    {
      title: '默认模型',
      dataIndex: 'default_model',
      key: 'default_model',
      render: (value: string, record: ModelConfig) => normalizeDefaultModel(record.provider, value),
    },
    {
      title: '可用状态',
      dataIndex: 'readiness_status',
      key: 'readiness_status',
      render: (status: ModelConfig['readiness_status'], record: ModelConfig) => (
        <Space direction="vertical" size={0}>
          <Tag color={readinessColor(status)}>{READINESS_LABELS[status]}</Tag>
          <Text type="secondary" className="settings-readiness-message">{record.readiness_message}</Text>
          {record.last_tested_at && (
            <Text type="secondary" className="settings-readiness-time">
              上次验证：{(formatApiDateTime(record.last_tested_at) || '时间未记录')}
            </Text>
          )}
        </Space>
      ),
    },
    {
      title: '凭据',
      key: 'credential',
      render: (_: unknown, record: ModelConfig) => (
        isLocalCliProvider(record.provider) ? '本机工具，无需 API Key' : '已加密保存'
      ),
    },
    {
      title: '全局默认',
      dataIndex: 'is_global_default',
      key: 'is_global_default',
      render: (_v: boolean, record: ModelConfig) =>
        globalModel.provider === record.provider
          ? <Tag icon={<CheckCircleOutlined />} color="success">是</Tag>
          : <span>—</span>,
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: ModelConfig) => (
        <Space wrap>
          <Button
            type={record.is_usable ? 'default' : 'primary'}
            icon={<ReloadOutlined />}
            loading={verifyingProvider === record.provider}
            onClick={() => void verifySavedConfig(record.provider)}
          >
            {record.is_usable ? '重新验证' : '测试并启用'}
          </Button>
          {record.is_usable && globalModel.provider !== record.provider && (
            <Button onClick={() => void handleSetGlobal(record.provider)}>设为默认</Button>
          )}
          <Button
            type="text"
            icon={<EditOutlined />}
            onClick={() => handleAddOrEdit(record.provider)}
          >
            编辑
          </Button>
          <Button
            type="text"
            danger
            icon={<DeleteOutlined />}
            onClick={() => handleDelete(record.provider)}
          >
            删除
          </Button>
        </Space>
      ),
    },
  ]

  const readyConfigs = configs.filter((config) => config.is_usable)
  const pendingConfigs = configs.filter((config) => !config.is_usable)

  const revealPendingModels = () => {
    setPendingModelsOpen(true)
    window.requestAnimationFrame(() => {
      document.getElementById('pending-model-configs')?.scrollIntoView({ block: 'nearest' })
    })
  }

  const defaultModelOptions = modelOptions.length > 0 ? modelOptions : fallbackModelOptions(modalProvider)
  const customModelSelection = isCustomProviderSelection(modalProvider)
  const customManualEntry = customModelSelection && modelDiscovery.status === 'manual'
  const providerOptions = launcherSettings?.gateway_headless
    ? PROVIDER_OPTIONS.filter((option) => !isLocalCliProvider(option.value))
    : PROVIDER_OPTIONS

  return (
    <div className="settings-page">
      {!embedded && <SystemNav current="settings" />}
      <header className="siming-section-header settings-heading">
        <div>
          <span className="siming-section-kicker">系统控制</span>
          <Title level={3}><SettingOutlined /> 系统设置</Title>
          <p className="siming-section-description">先连接一个可用模型；启动、更新和数据目录只在需要时调整。</p>
        </div>
      </header>
      <Tabs
        className="settings-tabs"
        activeKey={settingsSection}
        onChange={(key) => setSettingsSection(key as 'ai' | 'app' | 'gateway')}
        items={[
          { key: 'ai', label: '模型与 AI' },
          { key: 'gateway', label: '跨设备 Gateway' },
          { key: 'app', label: '应用与数据' },
        ].filter((item) => !launcherSettings?.gateway_headless || item.key !== 'app')}
      />

      {settingsSection === 'gateway' && (
        <GatewaySettingsPanel
          settings={launcherSettings}
          launcherLoading={launcherLoading}
          onSave={saveGatewaySettings}
        />
      )}

      {settingsSection === 'app' && <>
      <Card className="settings-card" title={<span><DesktopOutlined /> 启动方式</span>} loading={launcherLoading && !launcherSettings}>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Paragraph style={{ margin: 0 }}>
            选择司命下次启动时打开界面的方式。切换不会重启当前程序，也不会影响作品和模型配置。
          </Paragraph>
          <Radio.Group value={launchMode} onChange={(event) => setLaunchMode(event.target.value)}>
            <Space direction="vertical" size={8}>
              <Radio value="desktop">
                <Text strong>桌面窗口</Text>
                <Text type="secondary"> 使用内嵌 WebView2 打开司命。</Text>
              </Radio>
              <Radio value="browser">
                <Text strong>浏览器模式</Text>
                <Text type="secondary"> 司命只启动本地服务，并用默认浏览器打开，不启动内嵌 WebView2。</Text>
              </Radio>
            </Space>
          </Radio.Group>
          <Space wrap>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              aria-label="保存启动方式"
              loading={launcherLoading}
              disabled={launcherSettings?.launch_mode === launchMode}
              onClick={saveLaunchMode}
            >
              保存启动方式
            </Button>
            <Text type="secondary">当前保存：{launcherSettings?.launch_mode === 'browser' ? '浏览器模式' : '桌面窗口'}</Text>
          </Space>
          <Alert
            showIcon
            type="info"
            message="浏览器模式可避免由司命启动 Edge WebView2"
            description="它不能阻止浏览器自身更新，但能避免 Siming.exe 启动内嵌 WebView2 后被安全软件按父进程关联提示。"
          />
        </Space>
      </Card>

      <UpdateSettingsCard
        updateChannel={updateChannel}
        savedUpdateChannel={launcherSettings?.update_channel}
        updateStatus={updateStatus}
        launcherLoading={launcherLoading}
        checkingUpdate={checkingUpdate}
        downloadingUpdate={downloadingUpdate}
        installingUpdate={installingUpdate}
        onChannelChange={(channel) => {
          setUpdateChannel(channel)
          setUpdateStatus(null)
        }}
        onSaveChannel={saveUpdateChannel}
        onCheck={checkForUpdates}
        onDownload={downloadUpdate}
        onInstall={installUpdate}
      />

      <Card className="settings-card" title={<span><FolderOpenOutlined /> 小说数据目录</span>} loading={contentRootLoading && !contentRoot}>
        <Descriptions size="small" column={1} bordered>
          <Descriptions.Item label="当前目录">
            <Text code copyable>{contentRoot?.current_path || '未加载'}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="默认目录">
            <Text code copyable>{contentRoot?.default_path || '未加载'}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <Space wrap>
              <Tag color={contentRoot?.is_default ? 'default' : 'blue'}>
                {contentRoot?.is_default ? '使用默认目录' : '已指定目录'}
              </Tag>
              <Tag color={contentRoot?.exists ? 'success' : 'warning'}>
                {contentRoot?.exists ? '目录存在' : '目录未创建'}
              </Tag>
              <Tag color={contentRoot?.is_empty ? 'default' : 'green'}>
                {contentRoot?.is_empty ? '当前为空' : (contentRoot?.looks_like_siming_root || contentRoot?.looks_like_moshu_root) ? '司命数据目录' : '已有文件'}
              </Tag>
            </Space>
          </Descriptions.Item>
        </Descriptions>

        <Divider style={{ margin: '16px 0' }} />

        <Space.Compact style={{ width: '100%' }}>
          <Input
            value={contentRootPath}
            onChange={(event) => setContentRootPath(event.target.value)}
            placeholder="选择或填写一个空文件夹路径"
          />
          <Button icon={<FolderOpenOutlined />} loading={contentRootLoading} onClick={pickContentRoot}>
            选择文件夹
          </Button>
          <Button type="primary" icon={<SaveOutlined />} loading={contentRootLoading} onClick={saveContentRoot}>
            保存
          </Button>
        </Space.Compact>

        <p style={{ marginTop: 12, color: 'var(--ant-color-text-secondary)' }}>
          未指定时自动使用默认目录。切换目录会迁移现有作品资料；为了避免混入无关文件，新目录必须为空，或是已经由司命创建过的小说数据目录。
        </p>
      </Card>
      </>}

      {settingsSection === 'ai' && <>
      {launcherSettings?.gateway_headless && (
        <Alert
          showIcon
          type="info"
          message="Docker Gateway 只运行云端模型"
          description="本地模型、OpenCode 等本机 CLI、MCP 与训练能力仍留在桌面端；容器中只保存你主动配置的云端 API。"
          style={{ marginBottom: 16 }}
        />
      )}
      <ModelReadinessBanner
        ready={Boolean(globalModel.provider)}
        detail={globalModel.provider && globalModel.model
          ? `当前默认：${providerLabel(globalModel.provider)} · ${normalizeDefaultModel(globalModel.provider, globalModel.model)}`
          : pendingConfigs.length > 0
            ? `已检测到 ${pendingConfigs.length} 个配置。请先选择一个完成真实对话测试；仅检测到工具不代表已经登录或有可用额度。`
            : '先添加一个模型配置，再完成真实对话测试。测试成功后才会出现在创作入口中。'}
      />

      <Card
        className="settings-card"
        title="可用模型"
        extra={readyConfigs.length > 0 ? (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => handleAddOrEdit()}>
            添加配置
          </Button>
        ) : undefined}
      >
        {loading || readyConfigs.length > 0 ? (
          <Table
            dataSource={readyConfigs}
            columns={columns}
            rowKey="id"
            loading={loading}
            pagination={false}
            locale={{ emptyText: '还没有通过真实对话测试的模型' }}
            scroll={{ x: 900 }}
          />
        ) : (
          <div className="settings-model-empty" role="status">
            <Text strong className="settings-model-empty-title">
              {pendingConfigs.length > 0 ? '配置已经找到，下一步是验证可用性' : '先添加一个模型配置'}
            </Text>
            <Paragraph type="secondary">
              {pendingConfigs.length > 0
                ? '完成一次真实对话测试后，这个模型会自动进入新书立项和项目助手。'
                : '可以选择本机 CLI 或云端 API；保存后还需要完成一次真实对话测试。'}
            </Paragraph>
            <Space wrap>
              {pendingConfigs.length > 0 ? (
                <Button type="primary" onClick={revealPendingModels}>查看并验证现有配置</Button>
              ) : (
                <Button type="primary" icon={<PlusOutlined />} onClick={() => handleAddOrEdit()}>添加第一个配置</Button>
              )}
              {pendingConfigs.length > 0 && (
                <Button icon={<PlusOutlined />} onClick={() => handleAddOrEdit()}>添加其他配置</Button>
              )}
            </Space>
          </div>
        )}
      </Card>

      <Card
        className="settings-card"
        title="按任务选择模型"
      >
        <Alert
          showIcon
          type="info"
          message="同一个 API 可以按任务使用不同模型"
          description="优先级为：本次任务明确选择 > 任务默认 > 全局默认。下拉框会列出已通过连接验证的 API、CLI 和本地运行时所获取的模型。"
          style={{ marginBottom: 16 }}
        />
        <div className="settings-task-model-grid">
          {TASK_MODEL_CHOICES.map((task) => {
            const setting = modelConfigsQuery.data?.task_models?.[task.value]
            const value = setting
              ? `${setting.provider}:${normalizeDefaultModel(setting.provider, setting.model)}`
              : undefined
            return (
              <div className="settings-task-model-item" key={task.value}>
                <div>
                  <Text strong>{task.label}</Text>
                  <Paragraph type="secondary">{task.description}</Paragraph>
                </div>
                <Select
                  aria-label={`${task.label}默认模型`}
                  allowClear
                  showSearch
                  value={value}
                  options={availableTaskModelOptions}
                  loading={loading || savingTaskModel === task.value}
                  disabled={availableTaskModelOptions.length === 0 || Boolean(savingTaskModel)}
                  optionFilterProp="label"
                  placeholder="跟随全局默认模型"
                  notFoundContent={loading ? '正在读取模型列表' : '暂无可用模型'}
                  onChange={(next) => void saveTaskModel(task.value, next)}
                />
                {setting?.provider === 'local_llama_cpp' && value && (
                  <InputNumber
                    min={1}
                    max={1_000_000}
                    defaultValue={setting.context_length || undefined}
                    placeholder="按硬件自动选择上下文"
                    addonAfter="tokens"
                    aria-label={`${task.label}本地模型上下文`}
                    disabled={Boolean(savingTaskModel)}
                    onBlur={(event) => {
                      const parsed = Number(event.currentTarget.value)
                      void saveTaskModel(
                        task.value,
                        value,
                        Number.isFinite(parsed) && parsed > 0 ? parsed : null,
                      )
                    }}
                  />
                )}
              </div>
            )
          })}
        </div>
      </Card>

      <div id="pending-model-configs">
        <Collapse
          className="settings-card settings-pending-models"
          activeKey={pendingModelsOpen ? ['pending-models'] : []}
          onChange={(keys) => setPendingModelsOpen(keys.includes('pending-models'))}
          items={[{
            key: 'pending-models',
            label: <Space><Text strong>检测到但尚未可用</Text><Tag>{pendingConfigs.length}</Tag></Space>,
            children: (
              <>
                <Paragraph type="secondary">
                  这里的 CLI 或 API 配置尚未验证登录、模型和额度。测试成功前不会出现在助手、新书或写作模型列表中。
                </Paragraph>
                <Table
                  dataSource={pendingConfigs}
                  columns={columns}
                  rowKey="id"
                  loading={loading}
                  pagination={false}
                  locale={{ emptyText: '没有待验证的配置' }}
                  scroll={{ x: 900 }}
                />
              </>
            ),
          }]}
        />
      </div>

      {!launcherSettings?.gateway_headless && (
        <Card
          className="settings-card settings-cli-integrations"
          title={<span><ApiOutlined /> 本机 CLI 连接</span>}
          extra={(
            <Button
              icon={<DesktopOutlined />}
              aria-label={cliIntegrations ? '重新扫描本机 CLI' : '扫描本机 CLI'}
              loading={cliScanLoading}
              onClick={() => void scanCliIntegrations()}
            >
              {cliIntegrations ? '重新扫描' : '扫描本机 CLI'}
            </Button>
          )}
        >
          <Alert
            showIcon
            type="info"
            message="默认不扫描，也不修改其他 CLI"
            description="只有点击“扫描本机 CLI”后才会读取受支持 CLI 的安装与配置状态；扫描本身只读。找到后，你可以逐个决定是否自动配置或还原。"
          />
          {!cliIntegrations ? (
            <div className="settings-cli-unscanned" role="status">
              <DesktopOutlined />
              <div>
                <Text strong>尚未扫描本机 CLI</Text>
                <Paragraph type="secondary">司命没有在后台读取或修改 Claude Code、Codex、OpenCode 等工具的配置。</Paragraph>
              </div>
            </div>
          ) : cliIntegrations.clients.length === 0 ? (
            <div className="settings-cli-unscanned" role="status">
              <DesktopOutlined />
              <div><Text strong>未检测到受支持的 CLI</Text><Paragraph type="secondary">本次扫描未修改任何配置。</Paragraph></div>
            </div>
          ) : (
            <div className="settings-cli-list">
              {cliIntegrations.clients.map((client) => (
                <div className="settings-cli-item" key={client.provider}>
                  <div className="settings-cli-item-main">
                    <Space wrap>
                      <Text strong>{client.label}</Text>
                      <Tag color="blue">已检测到</Tag>
                      <Tag color={client.configured ? 'success' : 'default'}>{client.configured ? '已连接司命' : '尚未连接'}</Tag>
                    </Space>
                    <Text type="secondary" ellipsis={{ tooltip: client.command || client.config_path || undefined }}>
                      {client.command || client.config_path || '已找到配置目录'}
                    </Text>
                  </div>
                  <Space wrap className="settings-cli-item-actions">
                    <Button
                      type={client.configured ? 'default' : 'primary'}
                      icon={<SafetyCertificateOutlined />}
                      loading={cliAction === `configure:${client.provider}`}
                      disabled={Boolean(cliAction && cliAction !== `configure:${client.provider}`)}
                      aria-label={`${client.configured ? '重新配置' : '自动配置'} ${client.label}`}
                      onClick={() => configureCliIntegration(client)}
                    >
                      {client.configured ? '重新配置' : '自动配置'}
                    </Button>
                    <Button
                      icon={<RollbackOutlined />}
                      loading={cliAction === `restore:${client.provider}`}
                      disabled={!client.can_restore || Boolean(cliAction && cliAction !== `restore:${client.provider}`)}
                      aria-label={`还原 ${client.label}`}
                      onClick={() => restoreCliIntegration(client)}
                    >
                      还原
                    </Button>
                  </Space>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      <div id="context-governance-settings">
        <Collapse
          className="settings-card"
          defaultActiveKey={contextGovernanceRequested ? ['advanced-ai'] : []}
          items={[{
            key: 'advanced-ai',
            label: '高级设置：上下文与技术参数',
            children: <ContextGovernanceSettingsPanel />,
          }]}
        />
      </div>
      </>}

      <Modal
        title={editingProvider ? `编辑 ${providerLabel(editingProvider)} 配置` : '添加模型配置'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          style={{ marginTop: 16 }}
        >
          <Form.Item
            name="provider"
            label="提供商"
            rules={[{ required: true, message: '请选择提供商' }]}
          >
            <Select
              placeholder="选择提供商"
              disabled={!!editingProvider}
              onChange={(provider) => {
                const fallback = fallbackModelOptions(provider)
                const baseUrlOverride = isLocalCliProvider(provider)
                  ? undefined
                  : form.getFieldValue('base_url_override')
                setModelOptions(fallback)
                setModelDiscovery({ status: 'idle' })
                setConnectionTestResult(null)
                const nextModel = isCustomProviderSelection(provider) ? undefined : fallback[0]?.id
                form.setFieldValue('default_model', nextModel)
                form.setFieldsValue({
                  ...defaultSafetyLimits(
                    provider,
                    nextModel,
                    fallback[0],
                    baseUrlOverride,
                  ),
                  provider_type: isLocalCliProvider(provider) ? 'local_cli' : 'api',
                  cli_command: isLocalCliProvider(provider) ? DEFAULT_CLI_COMMANDS[provider] || '' : undefined,
                  cli_args: isLocalCliProvider(provider) ? DEFAULT_CLI_ARGS[provider] || '' : undefined,
                  api_key: isLocalCliProvider(provider) ? undefined : form.getFieldValue('api_key'),
                  base_url_override: baseUrlOverride,
                  api_protocol: isLocalCliProvider(provider) ? 'chat_completions' : 'auto',
                })
                if (isLocalCliProvider(provider) || form.getFieldValue('api_key')) {
                  void fetchModels(provider)
                }
              }}
              options={providerOptions}
            />
          </Form.Item>

          {isCustomProviderSelection(modalProvider) && (
            <Form.Item
              name="custom_provider"
              label="自定义提供商标识"
              extra="用于保存和选择模型，例如 openrouter、siliconflow、moonshot。只能包含字母、数字、下划线和短横线。"
              rules={[
                { required: true, message: '请填写自定义提供商标识' },
                {
                  pattern: PROVIDER_ID_PATTERN,
                  message: '只能包含字母、数字、下划线和短横线',
                },
              ]}
            >
              <Input
                disabled={!!editingProvider}
                placeholder="例如 openrouter"
                onBlur={() => {
                  if (form.getFieldValue('api_key')) {
                    fetchModels()
                  }
                }}
              />
            </Form.Item>
          )}

          {!isLocalCliProvider(modalProvider) && (
          <Form.Item
            name="api_key"
            label="API Key"
            extra={editingProvider ? '密钥已加密保存。留空会继续使用原密钥；只有输入新值才会替换。' : '密钥将在本机加密存储。'}
            rules={[{ required: !editingProvider, message: '请输入 API Key' }]}
          >
            <Input.Password
              placeholder={editingProvider ? '已保存，留空继续使用' : '输入 API Key（将被加密存储）'}
              onBlur={() => {
                if (form.getFieldValue('provider')) {
                  fetchModels()
                }
              }}
            />
          </Form.Item>
          )}

          {!isLocalCliProvider(modalProvider) && (
          <>
          <Form.Item
            name="base_url_override"
            label={customModelSelection ? 'API 端点' : '自定义 API 端点（可选）'}
            rules={[
              {
                required: customModelSelection,
                message: '自定义 OpenAI 兼容提供商必须填写 API 端点',
              },
            ]}
          >
            <Input
              placeholder="https://api.example.com/v1"
              onChange={(event) => {
                const provider = resolveProviderForSubmit(form.getFieldsValue())
                applyCapacityForModel(
                  provider,
                  form.getFieldValue('default_model'),
                  modelOptions,
                  event.target.value,
                )
              }}
              onBlur={() => {
                if (customModelSelection && form.getFieldValue('api_key')) {
                  void fetchModels()
                }
              }}
            />
          </Form.Item>
          <Form.Item
            name="api_protocol"
            label="API 协议"
            initialValue="auto"
            extra="推荐自动识别。若服务商文档写有 wire_api = responses、Responses API 或 Codex API，可直接选择 Responses API。"
          >
            <Select
              options={[
                { value: 'auto', label: '自动识别（推荐）' },
                { value: 'chat_completions', label: 'Chat Completions' },
                { value: 'responses', label: 'Responses API' },
              ]}
            />
          </Form.Item>
          {customModelSelection && (
            <Alert
              showIcon
              type={modelDiscovery.status === 'manual' ? 'warning' : modelDiscovery.status === 'success' ? 'success' : 'info'}
              message={modelsLoading ? '正在自动拉取模型列表…' : modelDiscovery.message || '填写 API 端点和 API Key 后，将自动拉取模型列表。'}
              action={modelDiscovery.status === 'manual' ? (
                <Button size="small" icon={<ReloadOutlined />} onClick={() => void fetchModels()}>
                  重新拉取
                </Button>
              ) : undefined}
              style={{ marginBottom: 16 }}
            />
          )}
          </>
          )}

          {isLocalCliProvider(modalProvider) && (
            <>
              <Alert
                showIcon
                type={modelDiscovery.status === 'manual' ? 'warning' : modelDiscovery.status === 'success' ? 'success' : 'info'}
                message={modelsLoading ? '正在获取 CLI 模型列表…' : modelDiscovery.message || '司命可以直接运行本机 CLI 获取可用模型。'}
                action={(
                  <Button size="small" icon={<ReloadOutlined />} loading={modelsLoading} onClick={() => void fetchModels()}>
                    {modalProvider === 'opencode_cli' ? '刷新 OpenCode 模型' : '刷新 CLI 模型'}
                  </Button>
                )}
                style={{ marginBottom: 16 }}
              />
              <Form.Item
                name="cli_command"
                label="本机 CLI 命令"
                extra="例如 claude、codex、opencode，或完整可执行文件路径。"
                rules={[{ required: modalProvider === 'custom_cli', message: '请填写本机 CLI 命令' }]}
              >
                <Input
                  placeholder={DEFAULT_CLI_COMMANDS[modalProvider] || 'my-agent-cli'}
                  onBlur={() => {
                    if (modalProvider && isLocalCliProvider(modalProvider)) {
                      fetchModels()
                    }
                  }}
                />
              </Form.Item>
              <Form.Item
                name="cli_args"
                label="CLI 参数"
                extra="JSON 数组或普通参数字符串。可使用 {prompt} 和 {model} 占位符；默认参数不绕过审批，项目权限只在聊天中由你单次授权。"
              >
                <Input.TextArea
                  rows={3}
                  placeholder={DEFAULT_CLI_ARGS[modalProvider] || '["{prompt}"]'}
                  onBlur={() => {
                    if (modalProvider && isLocalCliProvider(modalProvider)) {
                      fetchModels()
                    }
                  }}
                />
              </Form.Item>
              <Button
                type="link"
                size="small"
                icon={<ReloadOutlined spin={testingConnection} />}
                loading={testingConnection}
                onClick={testConnection}
                style={{ padding: 0, marginTop: -8, marginBottom: 12 }}
              >
                测试本机 CLI
              </Button>
            </>
          )}

          <Form.Item
            name="default_model"
            label="默认模型"
            extra={isLocalCliProvider(modalProvider) ? '列表由司命调用本机 CLI 自动获取；仍可直接输入模型名作为兜底。' : undefined}
            rules={[{ required: true, message: '请选择默认模型名' }]}
          >
            {isLocalCliProvider(modalProvider) ? (
              <AutoComplete
                options={defaultModelOptions.map((m) => ({
                  value: m.id,
                  label: m.display_name || m.id,
                }))}
                placeholder="输入 CLI 支持的模型名，或选择候选项"
                // In combobox mode AutoComplete uses the current value as its search text.
                // Keep every discovered model visible when an existing configuration is opened.
                filterOption={false}
                onChange={(modelName) => {
                  const provider = resolveProviderForSubmit(form.getFieldsValue())
                  form.setFieldsValue(defaultSafetyLimits(
                    provider,
                    modelName,
                    optionForModel(provider, modelName, defaultModelOptions),
                    form.getFieldValue('base_url_override'),
                  ))
                }}
              />
            ) : customManualEntry ? (
              <Input
                placeholder="例如 openai/gpt-4o-mini 或 vendor-model-name"
                onChange={(event) => {
                  const provider = resolveProviderForSubmit(form.getFieldsValue())
                  form.setFieldsValue(defaultSafetyLimits(
                    provider,
                    event.target.value,
                    undefined,
                    form.getFieldValue('base_url_override'),
                  ))
                }}
              />
            ) : (
              <Select
                showSearch
                loading={modelsLoading}
                disabled={customModelSelection && modelDiscovery.status === 'idle' && defaultModelOptions.length === 0}
                placeholder={
                  modelsLoading
                    ? '正在获取模型列表...'
                    : customModelSelection && modelDiscovery.status === 'idle'
                    ? '填写 API 端点和 API Key 后自动拉取'
                    : defaultModelOptions.length > 0
                    ? '选择模型名'
                    : '请先输入 API Key 以获取模型列表'
                }
                notFoundContent={
                  modelsLoading
                    ? '加载中...'
                    : form.getFieldValue('api_key') || editingProvider
                    ? '未找到模型'
                    : '请先输入 API Key'
                }
                filterOption={(input, option) =>
                  (option?.label as string)?.toLowerCase().includes(input.toLowerCase())
                }
                onChange={(modelName) => {
                  const provider = resolveProviderForSubmit(form.getFieldsValue())
                  form.setFieldsValue(defaultSafetyLimits(
                    provider,
                    modelName,
                    optionForModel(provider, modelName, defaultModelOptions),
                    form.getFieldValue('base_url_override'),
                  ))
                }}
                options={defaultModelOptions.map((m) => ({
                  value: m.id,
                  label: m.display_name || m.id,
                }))}
              />
            )}
          </Form.Item>

          <Form.Item
            name="context_window_tokens"
            label="模型上下文窗口 tokens"
            extra={contextProfileSource && contextProfileSource !== 'configured'
              ? '已按服务商公开规格或模型接口自动验证；模型切换时会同步更新。'
              : contextProfileSource === 'configured'
                ? '这是你已确认并保存的容量档案，可按当前模型文档修改。'
                : '可留空；司命会按 256,000 tokens 临时兜底。若知道服务商的实际窗口，可填写并优先使用该档案。'}
            rules={[
              ({ getFieldValue }) => ({
                validator: async (_, value) => {
                  if (!value) return
                  const output = Number(getFieldValue('max_output_tokens') || 0)
                  const margin = Number(getFieldValue('context_safety_margin_tokens') || 0)
                  if (output + margin >= Number(value)) {
                    throw new Error('上下文窗口必须大于最大输出与保护余量之和')
                  }
                },
              }),
            ]}
          >
            <InputNumber
              min={2048}
              max={10000000}
              disabled={Boolean(contextProfileSource && contextProfileSource !== 'configured')}
              style={{ width: '100%' }}
            />
          </Form.Item>
          <Form.Item name="context_safety_margin_tokens" initialValue={512} hidden>
            <InputNumber />
          </Form.Item>
          <Form.Item name="context_profile_source" hidden>
            <Input />
          </Form.Item>

          <Collapse
            ghost
            size="small"
            items={[{
              key: 'advanced',
              label: '高级设置（输出限制与拆书参数）',
              children: (
                <>
                  <Form.Item
                    name="max_output_tokens"
                    label="模型最大输出 tokens"
                    extra="默认按模型能力上限填充；DeepSeek v4-pro / v4-flash 默认为 384,000，Gemini 默认为 65,536。"
                    rules={[{ required: true, message: '请填写最大输出 tokens' }]}
                  >
                    <InputNumber min={1} max={1000000} style={{ width: '100%' }} />
                  </Form.Item>

                  <Form.Item
                    name="deconstruct_input_char_limit"
                    label="拆书合并输入字符上限"
                    extra="控制每次合并请求最多携带多少分块事实卡片内容。"
                    rules={[{ required: true, message: '请填写合并输入字符上限' }]}
                  >
                    <InputNumber min={1} max={1000000} style={{ width: '100%' }} />
                  </Form.Item>

                  <Form.Item
                    name="deconstruct_item_char_limit"
                    label="拆书单条内容字符上限"
                    extra="控制单条事件、设定、角色字段的最大长度；超过后才会压缩。"
                    rules={[{ required: true, message: '请填写单条内容字符上限' }]}
                  >
                    <InputNumber min={1} max={1000000} style={{ width: '100%' }} />
                  </Form.Item>
                </>
              ),
            }]}
          />

          {!isLocalCliProvider(modalProvider) && (
          <>
          <Button
            type="default"
            icon={<ReloadOutlined spin={testingConnection} />}
            loading={testingConnection}
            onClick={testConnection}
            style={{ marginBottom: 16 }}
          >
            用当前模型真实测试
          </Button>
          {connectionTestResult && (
            <div style={{
              marginTop: -8, marginBottom: 16, fontSize: 13,
              color: connectionTestResult.success ? '#52c41a' : '#ff4d4f',
            }}>
              {connectionTestResult.success
                ? <CheckCircleOutlined />
                : <CloseCircleOutlined />
              }
              {' '}{connectionTestResult.message}
            </div>
          )}
          </>
          )}
        </Form>
      </Modal>
    </div>
  )
}

export default SettingsPage
