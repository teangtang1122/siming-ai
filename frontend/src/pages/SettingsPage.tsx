import { useState, useEffect, useCallback } from 'react'
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
} from 'antd'
import {
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  GlobalOutlined,
  ReloadOutlined,
  FolderOpenOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { useAppStore } from '../stores'
import SystemNav from '../components/SystemNav'

const { Title, Paragraph, Text } = Typography

interface ModelConfig {
  id: string
  provider: string
  default_model: string
  is_global_default: boolean
  base_url_override?: string
  provider_type?: string
  cli_command?: string
  cli_args?: string
  max_output_tokens?: number | null
  effective_max_output_tokens?: number
  deconstruct_input_char_limit?: number | null
  effective_deconstruct_input_char_limit?: number
  deconstruct_item_char_limit?: number | null
  effective_deconstruct_item_char_limit?: number
  api_key_masked?: string
  created_at?: string
  updated_at?: string
}

interface GlobalModel {
  provider: string | null
  model: string | null
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

interface ModelOption {
  id: string
  display_name?: string
}

const PROVIDER_OPTIONS = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic Claude' },
  { value: 'deepseek', label: 'DeepSeek（v4-pro / v4-flash）' },
  { value: 'qwen', label: '通义千问' },
  { value: 'gemini', label: 'Google Gemini' },
  { value: 'claude_cli', label: 'Claude Code CLI（本机）' },
  { value: 'codex_cli', label: 'Codex CLI（本机）' },
  { value: 'opencode_cli', label: 'opencode CLI（本机）' },
  { value: 'mimocode_cli', label: 'MiMo Code CLI（本机）' },
  { value: 'cursor_cli', label: 'Cursor Agent CLI（本机）' },
  { value: 'kilocode_cli', label: 'Kilo Code CLI（本机）' },
  { value: 'qwen_code_cli', label: 'Qwen Code CLI（本机）' },
  { value: 'hermes_cli', label: 'Hermes Agent CLI（本机）' },
  { value: 'openclaw_cli', label: 'OpenClaw CLI（本机）' },
  { value: 'custom_cli', label: '自定义本机 CLI' },
  { value: '__custom_openai_compatible__', label: '自定义 OpenAI 兼容' },
]

const CUSTOM_PROVIDER_VALUE = '__custom_openai_compatible__'
const PROVIDER_ID_PATTERN = /^[A-Za-z0-9_-]+$/

const PROVIDER_LABEL_MAP: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic Claude',
  deepseek: 'DeepSeek',
  qwen: '通义千问',
  gemini: 'Google Gemini',
  local_llama_cpp: '司命本地 AI',
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
}

const PROVIDER_COLOR_MAP: Record<string, string> = {
  openai: 'green',
  anthropic: 'purple',
  deepseek: 'blue',
  qwen: 'orange',
  gemini: 'cyan',
  local_llama_cpp: 'green',
  claude_cli: 'purple',
  codex_cli: 'geekblue',
  opencode_cli: 'magenta',
  mimocode_cli: 'gold',
  cursor_cli: 'blue',
  kilocode_cli: 'volcano',
  qwen_code_cli: 'cyan',
  hermes_cli: 'purple',
  openclaw_cli: 'green',
  custom_cli: 'default',
}

const providerLabel = (provider?: string | null) => {
  if (!provider) return ''
  return PROVIDER_LABEL_MAP[provider] || provider
}

const providerColor = (provider?: string | null) => {
  if (!provider) return 'default'
  return PROVIDER_COLOR_MAP[provider] || 'default'
}

const isCustomProviderSelection = (provider?: string) => provider === CUSTOM_PROVIDER_VALUE
const LOCAL_CLI_PROVIDERS = [
  'claude_cli',
  'codex_cli',
  'opencode_cli',
  'mimocode_cli',
  'cursor_cli',
  'kilocode_cli',
  'qwen_code_cli',
  'hermes_cli',
  'openclaw_cli',
  'custom_cli',
]
const isLocalCliProvider = (provider?: string) => Boolean(provider && LOCAL_CLI_PROVIDERS.includes(provider))

const resolveProviderForSubmit = (values: any) => (
  isCustomProviderSelection(values.provider)
    ? String(values.custom_provider || '').trim()
    : values.provider
)

const DEEPSEEK_MODEL_OPTIONS: ModelOption[] = [
  { id: 'deepseek-v4-pro', display_name: 'deepseek-v4-pro' },
  { id: 'deepseek-v4-flash', display_name: 'deepseek-v4-flash' },
]

const GEMINI_MODEL_OPTIONS: ModelOption[] = [
  { id: 'gemini-3-pro-preview', display_name: 'gemini-3-pro-preview' },
  { id: 'gemini-3-flash-preview', display_name: 'gemini-3-flash-preview' },
  { id: 'gemini-2.5-pro', display_name: 'gemini-2.5-pro' },
  { id: 'gemini-2.5-flash', display_name: 'gemini-2.5-flash' },
  { id: 'gemini-2.5-flash-lite', display_name: 'gemini-2.5-flash-lite' },
]

const LOCAL_CLI_MODEL_OPTIONS: Record<string, ModelOption[]> = {
  claude_cli: [{ id: 'claude-code', display_name: 'claude-code' }],
  codex_cli: [{ id: 'codex-cli', display_name: 'codex-cli' }],
  opencode_cli: [{ id: 'opencode-cli', display_name: 'opencode-cli' }],
  mimocode_cli: [{ id: 'xiaomi/mimo-v2.5-pro', display_name: 'xiaomi/mimo-v2.5-pro' }],
  cursor_cli: [{ id: 'cursor-agent', display_name: 'cursor-agent' }],
  kilocode_cli: [{ id: 'kilocode-cli', display_name: 'kilocode-cli' }],
  qwen_code_cli: [{ id: 'qwen-code-cli', display_name: 'qwen-code-cli' }],
  hermes_cli: [{ id: 'hermes-agent', display_name: 'hermes-agent' }],
  openclaw_cli: [{ id: 'openclaw-agent', display_name: 'openclaw-agent' }],
  custom_cli: [{ id: 'custom-cli', display_name: 'custom-cli' }],
}

const DEFAULT_CLI_COMMANDS: Record<string, string> = {
  claude_cli: 'claude',
  codex_cli: 'codex',
  opencode_cli: 'opencode',
  mimocode_cli: 'mimo',
  cursor_cli: 'agent',
  kilocode_cli: 'kilo',
  qwen_code_cli: 'qwen',
  hermes_cli: 'hermes',
  openclaw_cli: 'openclaw',
  custom_cli: '',
}

const DEFAULT_CLI_ARGS: Record<string, string> = {
  claude_cli: '["--permission-mode","bypassPermissions","-p","{prompt}"]',
  codex_cli: '["exec","--dangerously-bypass-approvals-and-sandbox","{prompt}"]',
  opencode_cli: '["run","--dangerously-skip-permissions","{prompt}"]',
  mimocode_cli: '["run","--dangerously-skip-permissions","{prompt}"]',
  cursor_cli: '["-p","--force","--approve-mcps","--trust","--output-format","text","{prompt}"]',
  kilocode_cli: '["run","--auto","{prompt}"]',
  qwen_code_cli: '["--approval-mode","yolo","--output-format","text","{prompt}"]',
  hermes_cli: '["--yolo","--oneshot","{prompt}"]',
  openclaw_cli: '["agent","--local","--json","--session-key","agent:siming:local-cli","--message","{prompt}"]',
  custom_cli: '["{prompt}"]',
}

const SIMING_RELEASE_URL = 'https://github.com/teangtang1122/siming-ai/releases/latest'

const API_KEY_LINKS: Array<{ label: string; href: string; note: string }> = [
  { label: 'OpenAI API Key', href: 'https://platform.openai.com/api-keys', note: 'OpenAI / GPT 系列模型' },
  { label: 'Anthropic Console', href: 'https://platform.claude.com/', note: 'Claude 系列模型' },
  { label: 'DeepSeek API Key', href: 'https://platform.deepseek.com/api_keys', note: 'DeepSeek 官方 API' },
  { label: 'Google AI Studio', href: 'https://aistudio.google.com/app/apikey', note: 'Gemini 系列模型' },
  { label: 'Qwen / Model Studio', href: 'https://bailian.console.aliyun.com/', note: '通义千问 / 阿里百炼' },
]

const CLI_INSTALL_COMMANDS: Array<{ label: string; command: string; note: string; href?: string }> = [
  { label: 'Node.js LTS（Codex/opencode 前置）', command: '打开 Node.js 下载页', href: 'https://nodejs.org/zh-cn/download', note: '如果提示 npm 不是内部或外部命令，先安装这个。' },
  { label: 'Codex CLI', command: 'npm install -g @openai/codex', note: '安装后通常还需要在终端里运行 codex 登录。' },
  { label: 'Claude Code', command: 'winget install Anthropic.ClaudeCode', note: '如果 winget 不可用，可以先走 API Key 路线。' },
  { label: 'opencode', command: 'npm install -g opencode-ai', note: '安装后按该 CLI 的提示登录或配置提供方。' },
]

const TERMINAL_OPEN_STEPS = [
  '按键盘左下角的 Win 键，输入 PowerShell。',
  '点开「Windows PowerShell」或「终端」。如果弹出安全确认，选择允许。',
  '把安装命令复制进去，按 Enter 回车；安装完成后关闭再重新打开终端。',
  '回到司命「添加配置」，选择对应本机 CLI，点击「测试本机 CLI」。',
]

const FALLBACK_OUTPUT_LIMIT = 16000
const MODEL_OUTPUT_LIMITS: Record<string, number> = {
  'deepseek:deepseek-v4-pro': 384000,
  'deepseek:deepseek-v4-flash': 384000,
  'gemini:gemini-3-pro-preview': 65536,
  'gemini:gemini-3-flash-preview': 65536,
  'gemini:gemini-2.5-pro': 65536,
  'gemini:gemini-2.5-flash': 65536,
  'gemini:gemini-2.5-flash-lite': 65536,
}
const PROVIDER_OUTPUT_LIMITS: Record<string, number> = {
  deepseek: 384000,
  gemini: 65536,
}

const fallbackModelOptions = (provider?: string): ModelOption[] => {
  if (provider === 'deepseek') return DEEPSEEK_MODEL_OPTIONS
  if (provider === 'gemini') return GEMINI_MODEL_OPTIONS
  if (provider && LOCAL_CLI_MODEL_OPTIONS[provider]) return LOCAL_CLI_MODEL_OPTIONS[provider]
  return []
}

const normalizeDefaultModel = (provider: string, model: string) => {
  if (provider === 'deepseek' && model === 'deepseek-v3') {
    return 'deepseek-v4-flash'
  }
  if (provider === 'gemini' && model.startsWith('models/')) {
    return model.slice('models/'.length)
  }
  if (isLocalCliProvider(provider)) {
    return model || LOCAL_CLI_MODEL_OPTIONS[provider]?.[0]?.id || `${provider}-default`
  }
  return model
}

const isDeepSeekModelSupported = (model: string) => (
  DEEPSEEK_MODEL_OPTIONS.some((option) => option.id === model)
)

const normalizeProviderModelOptions = (provider: string, options: ModelOption[]) => {
  if (provider === 'gemini') {
    const normalized = options.map((option) => {
      const id = normalizeDefaultModel(provider, option.id)
      return {
        id,
        display_name: normalizeDefaultModel(provider, option.display_name || id),
      }
    })
    const unique = Array.from(new Map(normalized.map((option) => [option.id, option])).values())
    return unique.length > 0 ? unique : GEMINI_MODEL_OPTIONS
  }
  if (provider !== 'deepseek') return options
  const normalized = options
    .map((option) => ({
      id: normalizeDefaultModel(provider, option.id),
      display_name: normalizeDefaultModel(provider, option.display_name || option.id),
    }))
    .filter((option) => isDeepSeekModelSupported(option.id))
  const unique = Array.from(new Map(normalized.map((option) => [option.id, option])).values())
  return unique.length > 0 ? unique : DEEPSEEK_MODEL_OPTIONS
}

const defaultOutputLimit = (provider?: string, model?: string) => {
  if (!provider) return FALLBACK_OUTPUT_LIMIT
  const key = `${provider}:${model || ''}`
  return MODEL_OUTPUT_LIMITS[key] || PROVIDER_OUTPUT_LIMITS[provider] || FALLBACK_OUTPUT_LIMIT
}

const defaultSafetyLimits = (provider?: string, model?: string) => {
  const limit = defaultOutputLimit(provider, model)
  return {
    max_output_tokens: limit,
    deconstruct_input_char_limit: limit,
    deconstruct_item_char_limit: limit,
  }
}

interface SettingsPageProps {
  embedded?: boolean
}

function SettingsPage({ embedded = false }: SettingsPageProps = {}) {
  const { fetchProjects } = useAppStore()
  const [configs, setConfigs] = useState<ModelConfig[]>([])
  const [globalModel, setGlobalModel] = useState<GlobalModel>({ provider: null, model: null })
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingProvider, setEditingProvider] = useState<string | null>(null)
  const [form] = Form.useForm()
  const [globalForm] = Form.useForm()
  const modalProvider = Form.useWatch('provider', form)
  const globalSelectedProvider = Form.useWatch('provider', globalForm)

  const [modelOptions, setModelOptions] = useState<ModelOption[]>([])
  const [modelsLoading, setModelsLoading] = useState(false)
  const [testingConnection, setTestingConnection] = useState(false)
  const [connectionTestResult, setConnectionTestResult] = useState<{ success: boolean; message: string } | null>(null)
  const [contentRoot, setContentRoot] = useState<ContentRootSettings | null>(null)
  const [contentRootPath, setContentRootPath] = useState('')
  const [contentRootLoading, setContentRootLoading] = useState(false)

  const fetchConfigs = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiClient.get<{ code: number; data: { items: ModelConfig[] } }>('/config/models')
      setConfigs(res.data.data.items)
    } catch (err: any) {
      message.error(err.message || '获取模型配置失败')
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchGlobalModel = useCallback(async () => {
    try {
      const res = await apiClient.get<{ code: number; data: GlobalModel }>('/config/global-model')
      setGlobalModel(res.data.data)
      if (res.data.data.provider && res.data.data.model) {
        globalForm.setFieldsValue({
          provider: res.data.data.provider,
          model: normalizeDefaultModel(res.data.data.provider, res.data.data.model),
        })
      }
    } catch (err: any) {
      // ignore if not set
    }
  }, [globalForm])

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

  useEffect(() => {
    fetchConfigs()
    fetchGlobalModel()
    fetchContentRoot()
    fetchProjects()
  }, [fetchConfigs, fetchGlobalModel, fetchContentRoot, fetchProjects])

  const applyContentRootResponse = (settings: ContentRootSettings, successText: string) => {
    setContentRoot(settings)
    setContentRootPath(settings.current_path || settings.default_path || '')
    const migrated = settings.migration?.migrated_projects
    if (typeof migrated === 'number') {
      message.success(`${successText}，已迁移 ${migrated} 个作品`)
    } else {
      message.success(successText)
    }
    fetchProjects()
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
    if (provider) {
      const cfg = configs.find((c) => c.provider === provider)
      if (cfg) {
        setEditingProvider(provider)
        const defaultModel = normalizeDefaultModel(cfg.provider, cfg.default_model)
        const isKnownProvider = Boolean(PROVIDER_LABEL_MAP[cfg.provider])
        setModelOptions(fallbackModelOptions(cfg.provider))
        form.setFieldsValue({
          provider: isKnownProvider ? cfg.provider : CUSTOM_PROVIDER_VALUE,
          custom_provider: isKnownProvider ? undefined : cfg.provider,
          default_model: defaultModel,
          base_url_override: cfg.base_url_override || '',
          provider_type: cfg.provider_type || (isLocalCliProvider(cfg.provider) ? 'local_cli' : 'api'),
          cli_command: cfg.cli_command || DEFAULT_CLI_COMMANDS[cfg.provider] || '',
          cli_args: cfg.cli_args || DEFAULT_CLI_ARGS[cfg.provider] || '',
          api_key: '',
          max_output_tokens: cfg.max_output_tokens || cfg.effective_max_output_tokens || defaultOutputLimit(cfg.provider, defaultModel),
          deconstruct_input_char_limit: cfg.deconstruct_input_char_limit || cfg.effective_deconstruct_input_char_limit || defaultOutputLimit(cfg.provider, defaultModel),
          deconstruct_item_char_limit: cfg.deconstruct_item_char_limit || cfg.effective_deconstruct_item_char_limit || defaultOutputLimit(cfg.provider, defaultModel),
        })
      }
    } else {
      setEditingProvider(null)
      setModelOptions([])
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
        provider_type: isCli ? 'local_cli' : 'api',
        cli_command: isCli ? values.cli_command || DEFAULT_CLI_COMMANDS[provider] || null : null,
        cli_args: isCli ? values.cli_args || DEFAULT_CLI_ARGS[provider] || null : null,
        max_output_tokens: values.max_output_tokens || null,
        deconstruct_input_char_limit: values.deconstruct_input_char_limit || null,
        deconstruct_item_char_limit: values.deconstruct_item_char_limit || null,
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
          fetchGlobalModel()
        } catch (err: any) {
          message.error(err.message || '删除配置失败')
        }
      },
    })
  }

  const fetchModels = async () => {
    const provider = resolveProviderForSubmit(form.getFieldsValue())
    const apiKey = form.getFieldValue('api_key')
    if (!provider) return
    const isCli = isLocalCliProvider(provider)
    const baseUrl = form.getFieldValue('base_url_override') || undefined
    if (isCli) {
      setModelsLoading(true)
      setModelOptions(fallbackModelOptions(provider))
      try {
        const res = await apiClient.post<{ code: number; data: { models: ModelOption[] } }>(
          '/config/models/list',
          {
            provider,
            cli_command: form.getFieldValue('cli_command') || DEFAULT_CLI_COMMANDS[provider],
            cli_args: form.getFieldValue('cli_args') || DEFAULT_CLI_ARGS[provider],
          }
        )
        setModelOptions(normalizeProviderModelOptions(provider, res.data.data.models || []))
      } catch (err: any) {
        setModelOptions(fallbackModelOptions(provider))
      } finally {
        setModelsLoading(false)
      }
      return
    }
    if (!PROVIDER_LABEL_MAP[provider] && !baseUrl) {
      setModelOptions([])
      return
    }
    if (!apiKey) {
      setModelOptions(fallbackModelOptions(provider))
      return
    }

    setModelsLoading(true)
    setModelOptions(fallbackModelOptions(provider))
    try {
      const res = await apiClient.post<{ code: number; data: { models: ModelOption[] } }>(
        '/config/models/list',
        {
          provider,
          api_key: apiKey,
          base_url_override: baseUrl,
        }
      )
      setModelOptions(normalizeProviderModelOptions(provider, res.data.data.models || []))
    } catch (err: any) {
      setModelOptions(fallbackModelOptions(provider))
    } finally {
      setModelsLoading(false)
    }
  }

  const testConnection = async () => {
    const values = form.getFieldsValue()
    const provider = resolveProviderForSubmit(values)
    const isCli = isLocalCliProvider(provider)
    const apiKey = form.getFieldValue('api_key')
    if (!provider || (!isCli && !apiKey)) {
      message.warning('请先选择提供商并输入 API Key')
      return
    }
    const baseUrl = form.getFieldValue('base_url_override') || undefined
    if (!isCli && !PROVIDER_LABEL_MAP[provider] && !baseUrl) {
      message.warning('自定义 OpenAI 兼容提供商必须填写 API 端点')
      return
    }

    setTestingConnection(true)
    setConnectionTestResult(null)
    try {
      await apiClient.post('/config/models/test', {
        provider,
        api_key: isCli ? undefined : apiKey,
        base_url_override: isCli ? undefined : baseUrl,
        cli_command: isCli ? values.cli_command || DEFAULT_CLI_COMMANDS[provider] : undefined,
        cli_args: isCli ? values.cli_args || DEFAULT_CLI_ARGS[provider] : undefined,
        model: isCli ? values.default_model : undefined,
      })
      setConnectionTestResult({ success: true, message: isCli ? '本机 CLI 真实对话成功' : '连接成功，API Key 有效' })
    } catch (err: any) {
      setConnectionTestResult({ success: false, message: err.message || '连接失败' })
    } finally {
      setTestingConnection(false)
    }
  }

  const handleSetGlobal = async (values: { provider: string; model: string }) => {
    try {
      await apiClient.put('/config/global-model', {
        provider: values.provider,
        model: values.model,
      })
      message.success('全局默认模型已设置')
      fetchGlobalModel()
      fetchConfigs()
    } catch (err: any) {
      message.error(err.message || '设置全局默认模型失败')
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
      title: 'API Key',
      dataIndex: 'api_key_masked',
      key: 'api_key_masked',
      render: (v?: string) => v || '****',
    },
    {
      title: '全局默认',
      dataIndex: 'is_global_default',
      key: 'is_global_default',
      render: (v: boolean) =>
        v ? <Tag icon={<CheckCircleOutlined />} color="success">是</Tag> : <span>—</span>,
    },
    {
      title: '自定义端点',
      dataIndex: 'base_url_override',
      key: 'base_url_override',
      render: (v?: string) => v || '—',
    },
    {
      title: '安全长度',
      key: 'safety_limits',
      render: (_: any, record: ModelConfig) => (
        <Space size={4} wrap>
          <Tag>输出 {Number(record.effective_max_output_tokens || record.max_output_tokens || 0).toLocaleString()}</Tag>
          <Tag>合并输入 {Number(record.effective_deconstruct_input_char_limit || record.deconstruct_input_char_limit || 0).toLocaleString()}</Tag>
          <Tag>单条 {Number(record.effective_deconstruct_item_char_limit || record.deconstruct_item_char_limit || 0).toLocaleString()}</Tag>
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: ModelConfig) => (
        <Space>
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

  const availableProvidersForGlobal = configs.map((c) => ({
    value: c.provider,
    label: providerLabel(c.provider),
    model: c.default_model,
  }))

  const availableModelsForGlobal = configs
    .filter((c) => !globalSelectedProvider || c.provider === globalSelectedProvider)
    .map((c) => ({
      value: normalizeDefaultModel(c.provider, c.default_model),
      label: `${providerLabel(c.provider)} · ${normalizeDefaultModel(c.provider, c.default_model)}`,
    }))

  const defaultModelOptions = modelOptions.length > 0 ? modelOptions : fallbackModelOptions(modalProvider)

  return (
    <div style={{ padding: 24, maxWidth: 960, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <Title level={3} style={{ margin: 0 }}>⚙️ 系统设置</Title>
        {!embedded && <SystemNav current="settings" />}
      </div>

      <Card title="快速上手：API 与本机 CLI" style={{ marginTop: 16 }}>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Paragraph style={{ marginBottom: 0 }}>
            司命可以走两条路：配置 API Key 后由司命直接调用模型；或安装 Codex/Claude Code/opencode 等本机 CLI，
            由司命启动 CLI Agent。CLI 可以直接读取作品镜像文件，但写入章节、角色、大纲和世界观必须调用司命工具入库，
            入库后系统会自动同步回文件目录。
          </Paragraph>
          <div>
            <Text strong>第一次使用：从下载到能对话</Text>
            <ol style={{ margin: '6px 0 8px', paddingLeft: 20 }}>
              <li>点击下面的按钮打开 GitHub Release 页面，下载最新的 Siming.exe。</li>
              <li>双击 Siming.exe 启动；如果 Windows 提示未知发布者，确认这是你从项目 Release 下载的文件后再继续。</li>
              <li>进入「系统设置」，先选 API Key 路线；没有命令行经验时，这条路最稳。</li>
              <li>在模型平台创建 API Key，复制到「添加配置」，点「测试连接」。成功后设为全局默认模型。</li>
              <li>回到作品页，先让助手做一次小任务；确认能回复后再进行建档、写章或本机 CLI 配置。</li>
            </ol>
            <Button size="small" type="primary" href={SIMING_RELEASE_URL} target="_blank" rel="noreferrer">
              点击打开：司命最新版下载页
            </Button>
          </div>
          <div>
            <Text strong>0. 电脑里什么都还没有时：</Text>
            <Paragraph style={{ margin: '4px 0 8px' }}>
              建议先走 API Key 路线，只需要浏览器和一个模型平台账号。想使用本机 CLI 时，再安装 Node.js 或对应 CLI。
              下面所有蓝色按钮都可以点击打开网页，命令右侧的复制图标可以一键复制。
            </Paragraph>
          </div>
          <div>
            <Text strong>1. 使用 API 模型：</Text>
            <Paragraph style={{ margin: '4px 0 8px' }}>
              到对应平台创建 API Key，然后点击下方「添加配置」，选择提供商，填入 API Key，点击「测试连接」，最后设为全局默认模型。
            </Paragraph>
            <Space direction="vertical" size={6} style={{ width: '100%' }}>
              {API_KEY_LINKS.map((item) => (
                <Space key={item.href} wrap>
                  <Button size="small" type="link" href={item.href} target="_blank" rel="noreferrer">
                    点击打开：{item.label}
                  </Button>
                  <Text type="secondary">{item.note}</Text>
                  <Text code copyable>{item.href}</Text>
                </Space>
              ))}
            </Space>
          </div>
          <div>
            <Text strong>2. 使用本机 CLI：</Text>
            <Paragraph style={{ margin: '4px 0 8px' }}>
              先在终端安装并登录目标 CLI，再在「添加配置」里选择对应本机 CLI，确认命令和参数，点击「测试本机 CLI」。
            </Paragraph>
            <Text type="secondary">不知道怎么打开终端时，按这个顺序来：</Text>
            <ol style={{ margin: '6px 0 10px', paddingLeft: 20 }}>
              {TERMINAL_OPEN_STEPS.map((step) => <li key={step}>{step}</li>)}
            </ol>
            <Space direction="vertical" size={6} style={{ width: '100%' }}>
              {CLI_INSTALL_COMMANDS.map((item) => (
                <Text key={item.label}>
                  {item.label}：{item.href ? (
                    <Button size="small" type="link" href={item.href} target="_blank" rel="noreferrer">
                      点击打开：{item.command}
                    </Button>
                  ) : (
                    <Text code copyable>{item.command}</Text>
                  )}
                  <Text type="secondary"> {item.note}</Text>
                </Text>
              ))}
            </Space>
          </div>
          <div>
            <Text strong>3. 填到司命里：</Text>
            <Paragraph style={{ margin: '4px 0 0' }}>
              API 路线填「提供商、模型、API Key」后测试连接；CLI 路线填「本机 CLI 命令、CLI 参数」后测试本机 CLI。
              测试成功后点「设为全局默认」，AI 助手和写章计划就会优先使用它。
            </Paragraph>
          </div>
          <Text type="secondary">
            如果 CLI 自己创建或修改 chapters/*.md，前端不会显示；这表示它绕过了数据库。写作计划会校验这一点并提示修复。
          </Text>
        </Space>
      </Card>

      <Card title={<span><FolderOpenOutlined /> 小说数据目录</span>} style={{ marginTop: 16 }} loading={contentRootLoading && !contentRoot}>
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

        <p style={{ marginTop: 12, color: '#888' }}>
          未指定时自动使用默认目录。切换目录会迁移现有作品资料；为了避免混入无关文件，新目录必须为空，或是已经由司命创建过的小说数据目录。
        </p>
      </Card>

      <Card
        title="模型提供商配置"
        style={{ marginTop: 16 }}
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => handleAddOrEdit()}>
            添加配置
          </Button>
        }
      >
        <Table
          dataSource={configs}
          columns={columns}
          rowKey="id"
          loading={loading}
          pagination={false}
          locale={{ emptyText: '暂无模型配置，请点击右上角添加' }}
        />
      </Card>

      <Card title={<span><GlobalOutlined /> 全局默认模型</span>} style={{ marginTop: 16 }}>
        {globalModel.provider ? (
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="当前全局默认提供商">
              <Tag color={providerColor(globalModel.provider)}>
                {providerLabel(globalModel.provider)}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="当前全局默认模型">
              {globalModel.provider && globalModel.model ? normalizeDefaultModel(globalModel.provider, globalModel.model) : globalModel.model}
            </Descriptions.Item>
          </Descriptions>
        ) : (
          <p style={{ color: '#999' }}>尚未设置全局默认模型</p>
        )}

        <Divider style={{ margin: '16px 0' }} />

        <Form
          form={globalForm}
          layout="inline"
          onFinish={handleSetGlobal}
        >
          <Form.Item
            name="provider"
            label="选择提供商"
            rules={[{ required: true, message: '请选择提供商' }]}
          >
            <Select
              style={{ width: 180 }}
              placeholder="选择提供商"
              options={availableProvidersForGlobal}
              onChange={(val) => {
                const cfg = configs.find((c) => c.provider === val)
                globalForm.setFieldsValue({ model: cfg ? normalizeDefaultModel(cfg.provider, cfg.default_model) : undefined })
              }}
            />
          </Form.Item>
          <Form.Item
            name="model"
            label="模型名"
            rules={[{ required: true, message: '请选择模型名' }]}
          >
            <Select
              style={{ width: 240 }}
              placeholder="从已配置模型中选择"
              options={availableModelsForGlobal}
              optionFilterProp="label"
              showSearch
              notFoundContent="暂无已配置模型"
            />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit">
              设为全局默认
            </Button>
          </Form.Item>
        </Form>
      </Card>

      <Modal
        title={editingProvider ? `编辑 ${providerLabel(editingProvider)} 配置` : '添加模型配置'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        destroyOnClose
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
                setModelOptions(fallback)
                setConnectionTestResult(null)
                const nextModel = isCustomProviderSelection(provider) ? undefined : fallback[0]?.id
                form.setFieldValue('default_model', nextModel)
                form.setFieldsValue({
                  ...defaultSafetyLimits(provider, nextModel),
                  provider_type: isLocalCliProvider(provider) ? 'local_cli' : 'api',
                  cli_command: isLocalCliProvider(provider) ? DEFAULT_CLI_COMMANDS[provider] || '' : undefined,
                  cli_args: isLocalCliProvider(provider) ? DEFAULT_CLI_ARGS[provider] || '' : undefined,
                  api_key: isLocalCliProvider(provider) ? undefined : form.getFieldValue('api_key'),
                  base_url_override: isLocalCliProvider(provider) ? undefined : form.getFieldValue('base_url_override'),
                })
                if (isLocalCliProvider(provider) || form.getFieldValue('api_key')) {
                  fetchModels()
                }
              }}
              options={PROVIDER_OPTIONS}
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
            label={
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                API Key
                <Button
                  type="link"
                  size="small"
                  icon={<ReloadOutlined spin={testingConnection} />}
                  loading={testingConnection}
                  onClick={testConnection}
                  style={{ padding: 0 }}
                >
                  测试连接
                </Button>
              </span>
            }
            rules={[{ required: true, message: '请输入 API Key' }]}
          >
            <Input.Password
              placeholder="输入 API Key（将被加密存储）"
              onBlur={() => {
                if (form.getFieldValue('provider')) {
                  fetchModels()
                }
              }}
            />
          </Form.Item>
          )}

          {isLocalCliProvider(modalProvider) && (
            <>
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
                extra="JSON 数组或普通参数字符串。可使用 {prompt} 和 {model} 占位符。"
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

          {connectionTestResult && (
            <div style={{
              marginTop: -16, marginBottom: 16, fontSize: 13,
              color: connectionTestResult.success ? '#52c41a' : '#ff4d4f',
            }}>
              {connectionTestResult.success
                ? <CheckCircleOutlined />
                : <CloseCircleOutlined />
              }
              {' '}{connectionTestResult.message}
            </div>
          )}

          <Form.Item
            name="default_model"
            label="默认模型"
            extra={isLocalCliProvider(modalProvider) ? '本机 CLI 可直接输入 CLI 支持的模型名；占位模型表示跟随 CLI 自身默认。' : undefined}
            rules={[{ required: true, message: '请选择默认模型名' }]}
          >
            {isLocalCliProvider(modalProvider) ? (
              <AutoComplete
                options={defaultModelOptions.map((m) => ({
                  value: m.id,
                  label: m.display_name || m.id,
                }))}
                placeholder="输入 CLI 支持的模型名，或选择候选项"
                filterOption={(input, option) =>
                  String(option?.label || option?.value || '').toLowerCase().includes(input.toLowerCase())
                }
                onChange={(modelName) => {
                  const provider = resolveProviderForSubmit(form.getFieldsValue())
                  form.setFieldsValue(defaultSafetyLimits(provider, modelName))
                }}
              />
            ) : isCustomProviderSelection(modalProvider) && defaultModelOptions.length === 0 ? (
              <Input
                placeholder="例如 openai/gpt-4o-mini 或 vendor-model-name"
                onChange={(event) => {
                  const provider = resolveProviderForSubmit(form.getFieldsValue())
                  form.setFieldsValue(defaultSafetyLimits(provider, event.target.value))
                }}
              />
            ) : (
              <Select
                showSearch
                loading={modelsLoading}
                placeholder={
                  modelsLoading
                    ? '正在获取模型列表...'
                    : defaultModelOptions.length > 0
                    ? '选择模型名'
                    : '请先输入 API Key 以获取模型列表'
                }
                notFoundContent={
                  modelsLoading
                    ? '加载中...'
                    : form.getFieldValue('api_key')
                    ? '未找到模型'
                    : '请先输入 API Key'
                }
                filterOption={(input, option) =>
                  (option?.label as string)?.toLowerCase().includes(input.toLowerCase())
                }
                onChange={(modelName) => {
                  const provider = resolveProviderForSubmit(form.getFieldsValue())
                  form.setFieldsValue(defaultSafetyLimits(provider, modelName))
                }}
                options={defaultModelOptions.map((m) => ({
                  value: m.id,
                  label: m.display_name || m.id,
                }))}
              />
            )}
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
          <Form.Item
            name="base_url_override"
            label={isCustomProviderSelection(modalProvider) ? 'API 端点' : '自定义 API 端点（可选）'}
            rules={[
              {
                required: isCustomProviderSelection(modalProvider),
                message: '自定义 OpenAI 兼容提供商必须填写 API 端点',
              },
            ]}
          >
            <Input
              placeholder="https://api.example.com/v1"
              onBlur={() => {
                if (isCustomProviderSelection(form.getFieldValue('provider')) && form.getFieldValue('api_key')) {
                  fetchModels()
                }
              }}
            />
          </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  )
}

export default SettingsPage
