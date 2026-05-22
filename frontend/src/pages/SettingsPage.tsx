import { useState, useEffect, useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  Card,
  Typography,
  Table,
  Button,
  Modal,
  Form,
  Input,
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
  ArrowLeftOutlined,
  HomeOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { useAppStore } from '../stores'

const { Title } = Typography

interface ModelConfig {
  id: string
  provider: string
  default_model: string
  is_global_default: boolean
  base_url_override?: string
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

interface ModelOption {
  id: string
  display_name?: string
}

const PROVIDER_OPTIONS = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic Claude' },
  { value: 'deepseek', label: 'DeepSeek（v4-pro / v4-flash）' },
  { value: 'qwen', label: '通义千问' },
]

const PROVIDER_LABEL_MAP: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic Claude',
  deepseek: 'DeepSeek',
  qwen: '通义千问',
}

const PROVIDER_COLOR_MAP: Record<string, string> = {
  openai: 'green',
  anthropic: 'purple',
  deepseek: 'blue',
  qwen: 'orange',
}

const DEEPSEEK_MODEL_OPTIONS: ModelOption[] = [
  { id: 'deepseek-v4-pro', display_name: 'deepseek-v4-pro' },
  { id: 'deepseek-v4-flash', display_name: 'deepseek-v4-flash' },
]

const FALLBACK_OUTPUT_LIMIT = 16000
const MODEL_OUTPUT_LIMITS: Record<string, number> = {
  'deepseek:deepseek-v4-pro': 384000,
  'deepseek:deepseek-v4-flash': 384000,
}
const PROVIDER_OUTPUT_LIMITS: Record<string, number> = {
  deepseek: 384000,
}

const fallbackModelOptions = (provider?: string): ModelOption[] => (
  provider === 'deepseek' ? DEEPSEEK_MODEL_OPTIONS : []
)

const normalizeDefaultModel = (provider: string, model: string) => {
  if (provider === 'deepseek' && model === 'deepseek-v3') {
    return 'deepseek-v4-flash'
  }
  return model
}

const isDeepSeekModelSupported = (model: string) => (
  DEEPSEEK_MODEL_OPTIONS.some((option) => option.id === model)
)

const normalizeProviderModelOptions = (provider: string, options: ModelOption[]) => {
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

function SettingsPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { projects, fetchProjects } = useAppStore()
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
  const fromProjectId = (location.state as { fromProjectId?: string } | null)?.fromProjectId
  const returnProjectId = fromProjectId || projects[0]?.id

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

  useEffect(() => {
    fetchConfigs()
    fetchGlobalModel()
    fetchProjects()
  }, [fetchConfigs, fetchGlobalModel, fetchProjects])

  const handleAddOrEdit = (provider?: string) => {
    setConnectionTestResult(null)
    if (provider) {
      const cfg = configs.find((c) => c.provider === provider)
      if (cfg) {
        setEditingProvider(provider)
        const defaultModel = normalizeDefaultModel(cfg.provider, cfg.default_model)
        setModelOptions(fallbackModelOptions(cfg.provider))
        form.setFieldsValue({
          provider: cfg.provider,
          default_model: defaultModel,
          base_url_override: cfg.base_url_override || '',
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
      const defaultModel = normalizeDefaultModel(values.provider, values.default_model)
      if (values.provider === 'deepseek' && !isDeepSeekModelSupported(defaultModel)) {
        message.error('DeepSeek 当前支持 deepseek-v4-pro 或 deepseek-v4-flash，请重新选择')
        return
      }
      await apiClient.post('/config/models', {
        provider: values.provider,
        api_key: values.api_key,
        default_model: defaultModel,
        base_url_override: values.base_url_override || null,
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
      content: `确定要删除 ${PROVIDER_LABEL_MAP[provider] || provider} 的配置吗？`,
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
    const provider = form.getFieldValue('provider')
    const apiKey = form.getFieldValue('api_key')
    if (!provider) return
    if (!apiKey) {
      setModelOptions(fallbackModelOptions(provider))
      return
    }

    setModelsLoading(true)
    setModelOptions(fallbackModelOptions(provider))
    try {
      const baseUrl = form.getFieldValue('base_url_override') || undefined
      const res = await apiClient.post<{ code: number; data: { models: ModelOption[] } }>(
        '/config/models/list',
        { provider, api_key: apiKey, base_url_override: baseUrl }
      )
      setModelOptions(normalizeProviderModelOptions(provider, res.data.data.models || []))
    } catch (err: any) {
      setModelOptions(fallbackModelOptions(provider))
    } finally {
      setModelsLoading(false)
    }
  }

  const testConnection = async () => {
    const provider = form.getFieldValue('provider')
    const apiKey = form.getFieldValue('api_key')
    if (!provider || !apiKey) {
      message.warning('请先选择提供商并输入 API Key')
      return
    }

    setTestingConnection(true)
    setConnectionTestResult(null)
    try {
      const baseUrl = form.getFieldValue('base_url_override') || undefined
      await apiClient.post('/config/models/test', {
        provider,
        api_key: apiKey,
        base_url_override: baseUrl,
      })
      setConnectionTestResult({ success: true, message: '连接成功，API Key 有效' })
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
        <Tag color={PROVIDER_COLOR_MAP[v] || 'default'}>{PROVIDER_LABEL_MAP[v] || v}</Tag>
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
    label: PROVIDER_LABEL_MAP[c.provider] || c.provider,
    model: c.default_model,
  }))

  const availableModelsForGlobal = configs
    .filter((c) => !globalSelectedProvider || c.provider === globalSelectedProvider)
    .map((c) => ({
      value: normalizeDefaultModel(c.provider, c.default_model),
      label: `${PROVIDER_LABEL_MAP[c.provider] || c.provider} · ${normalizeDefaultModel(c.provider, c.default_model)}`,
    }))

  const defaultModelOptions = modelOptions.length > 0 ? modelOptions : fallbackModelOptions(modalProvider)

  return (
    <div style={{ padding: 24, maxWidth: 960, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <Title level={3} style={{ margin: 0 }}>⚙️ 系统设置</Title>
        <Space wrap>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => returnProjectId ? navigate(`/project/${returnProjectId}`) : navigate('/dashboard')}
          >
            返回作品
          </Button>
          <Button icon={<HomeOutlined />} onClick={() => navigate('/dashboard')}>
            作品管理
          </Button>
        </Space>
      </div>

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
              <Tag color={PROVIDER_COLOR_MAP[globalModel.provider] || 'default'}>
                {PROVIDER_LABEL_MAP[globalModel.provider] || globalModel.provider}
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
        title={editingProvider ? `编辑 ${PROVIDER_LABEL_MAP[editingProvider] || editingProvider} 配置` : '添加模型配置'}
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
                const nextModel = fallback[0]?.id
                form.setFieldValue('default_model', nextModel)
                form.setFieldsValue(defaultSafetyLimits(provider, nextModel))
                if (form.getFieldValue('api_key')) {
                  fetchModels()
                }
              }}
              options={PROVIDER_OPTIONS}
            />
          </Form.Item>

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
            rules={[{ required: true, message: '请选择默认模型名' }]}
          >
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
                const provider = form.getFieldValue('provider')
                form.setFieldsValue(defaultSafetyLimits(provider, modelName))
              }}
              options={defaultModelOptions.map((m) => ({
                value: m.id,
                label: m.display_name || m.id,
              }))}
            />
          </Form.Item>

          <Divider style={{ margin: '8px 0 16px' }} />

          <Form.Item
            name="max_output_tokens"
            label="模型最大输出 tokens"
            extra="默认按模型能力上限填充；DeepSeek v4-pro / v4-flash 默认为 384,000。"
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

          <Form.Item
            name="base_url_override"
            label="自定义 API 端点（可选）"
          >
            <Input placeholder="https://api.example.com/v1" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default SettingsPage
