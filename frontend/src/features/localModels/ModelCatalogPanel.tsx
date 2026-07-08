import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Progress,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  CloudDownloadOutlined,
  DeleteOutlined,
  ExperimentOutlined,
  PlayCircleOutlined,
  PoweroffOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { apiClient } from '../../api/client'
import type { CatalogResponse, DownloadTask, HardwareProfile, LocalModel } from './types'

const { Text } = Typography

const formatBytes = (value?: number | null) => {
  if (!value) return '未知'
  const units = ['B', 'KB', 'MB', 'GB']
  let current = value
  let index = 0
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024
    index += 1
  }
  return `${current.toFixed(index >= 3 ? 1 : 0)} ${units[index]}`
}

interface Props {
  hardware: HardwareProfile | null
  catalog: CatalogResponse | null
  downloads: DownloadTask[]
  loading: boolean
  onRefresh: () => Promise<void>
}

export default function ModelCatalogPanel({ hardware, catalog, downloads, loading, onRefresh }: Props) {
  const [modelRoot, setModelRoot] = useState('')
  const usageEnabled = catalog?.usage_enabled !== false
  const usageDisabledReason = catalog?.usage_disabled_reason || '本地 AI 模型暂时已停用，请使用 API 或本机 CLI 模型。'

  const contextForModel = (modelKey?: string | null) => (
    catalog?.items.find((item) => item.model_key === modelKey)?.context_length
    || hardware?.recommended_context
    || 8192
  )

  useEffect(() => {
    setModelRoot(catalog?.model_root || '')
  }, [catalog?.model_root])

  const saveModelRoot = async () => {
    try {
      await apiClient.put('/local-models/root', { path: modelRoot })
      message.success('模型目录已更新')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const install = async (model: LocalModel) => {
    try {
      await apiClient.post('/local-models/install', { model_key: model.model_key })
      message.success('下载任务已创建，支持断点续传')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const start = async (model: LocalModel) => {
    try {
      await apiClient.post('/local-models/runtime/start', {
        model_key: model.model_key,
        context_length: contextForModel(model.model_key),
        task_type: 'chat',
      })
      message.success('本地模型已加载')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const stop = async () => {
    await apiClient.post('/local-models/runtime/stop')
    await onRefresh()
  }

  const remove = async (model: LocalModel) => {
    await apiClient.delete(`/local-models/${model.model_key}`)
    message.success('模型文件已删除')
    await onRefresh()
  }

  const makeDefault = async (model: LocalModel) => {
    await apiClient.put('/config/global-model', {
      provider: 'local_llama_cpp',
      model: model.model_key,
    })
    message.success('已设为全局默认离线模型')
  }

  const benchmark = async (model: LocalModel) => {
    const hide = message.loading('正在进行中文生成测速...', 0)
    try {
      const response = await apiClient.post<{ data: any }>('/local-models/benchmark', {
        model_key: model.model_key,
        max_tokens: 128,
      })
      const result = response.data.data
      message.success(
        result.tokens_per_second
          ? `${result.tokens_estimated ? '约 ' : ''}${result.tokens_per_second} token/s，用时 ${result.elapsed_seconds}s`
          : `测速完成，用时 ${result.elapsed_seconds}s`,
      )
    } catch (error: any) {
      message.error(error.message)
    } finally {
      hide()
    }
  }

  const saveTaskModel = async (task: string, modelKey?: string | null) => {
    if (!modelKey) {
      await apiClient.delete(`/local-models/task-settings/${task}`)
      message.success('任务模型已清除，将跟随全局默认模型')
      await onRefresh()
      return
    }
    await apiClient.put(`/local-models/task-settings/${task}`, {
      model_key: modelKey,
      context_length: contextForModel(modelKey),
      allow_api_fallback: false,
    })
    message.success('任务模型已保存')
    await onRefresh()
  }

  const activeDownloads = downloads.filter((item) => !['completed', 'failed', 'cancelled'].includes(item.status))

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {!usageEnabled && (
        <Alert
          type="warning"
          showIcon
          message="本地 AI 暂停使用"
          description={usageDisabledReason}
        />
      )}

      {hardware && (
        <Card size="small" title="硬件与推荐">
          <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 4 }}>
            <Descriptions.Item label="显卡">{hardware.gpu_name || 'CPU 推理'}</Descriptions.Item>
            <Descriptions.Item label="显存">{hardware.vram_gb || 0} GB</Descriptions.Item>
            <Descriptions.Item label="内存">{hardware.ram_gb} GB</Descriptions.Item>
            <Descriptions.Item label="推荐">
              <Tag color="blue">{hardware.recommended_model}</Tag>
              {hardware.recommended_context / 1024}K 上下文
            </Descriptions.Item>
          </Descriptions>
          {!hardware.training_supported && (
            <Alert
              style={{ marginTop: 12 }}
              type="info"
              showIcon
              message="当前设备可以本地推理；LoRA 训练 Beta 需要至少 8GB 显存的 NVIDIA 显卡。"
            />
          )}
        </Card>
      )}

      <Card size="small" title="模型存储目录">
        <Space.Compact style={{ width: '100%' }}>
          <Input value={modelRoot} onChange={(event) => setModelRoot(event.target.value)} />
          <Button onClick={saveModelRoot}>保存</Button>
        </Space.Compact>
      </Card>

      {activeDownloads.length > 0 && (
        <Card size="small" title="下载进度">
          <Space direction="vertical" style={{ width: '100%' }}>
            {activeDownloads.map((task) => {
              const percent = task.total_bytes
                ? Math.min(100, Math.round(task.downloaded_bytes / task.total_bytes * 100))
                : 0
              return (
                <div key={task.id}>
                  <Space style={{ marginBottom: 4 }}>
                    <Text strong>{task.target_key}</Text>
                    <Tag>{task.kind === 'runtime' ? '运行时' : '模型'}</Tag>
                    <Text type="secondary">
                      {formatBytes(task.downloaded_bytes)} / {formatBytes(task.total_bytes)}
                    </Text>
                  </Space>
                  <Progress percent={percent} status={task.status === 'failed' ? 'exception' : 'active'} />
                </div>
              )
            })}
          </Space>
        </Card>
      )}

      <Card
        size="small"
        title="模型目录"
        extra={catalog?.runtime.running ? (
          <Button icon={<PoweroffOutlined />} onClick={stop}>停止运行时</Button>
        ) : null}
      >
        <Table
          rowKey="model_key"
          loading={loading}
          pagination={false}
          dataSource={catalog?.items || []}
          columns={[
            {
              title: '模型',
              render: (_, model: LocalModel) => (
                <Space direction="vertical" size={0}>
                  <Text strong>{model.display_name}</Text>
                  <Text type="secondary">{model.model_key}</Text>
                </Space>
              ),
            },
            {
              title: '规格',
              width: 170,
              render: (_, model: LocalModel) => (
                <Space wrap>
                  <Tag>{model.parameter_size}</Tag>
                  <Tag>{model.quantization}</Tag>
                  <Tag>{model.license_name}</Tag>
                </Space>
              ),
            },
            {
              title: '建议硬件',
              width: 130,
              render: (_, model: LocalModel) => `${model.recommended_vram_gb || 0}GB 显存`,
            },
            {
              title: '状态',
              width: 120,
              render: (_, model: LocalModel) => {
                const running = catalog?.runtime.running && catalog.runtime.model_key === model.model_key
                if (running) return <Tag color="processing">运行中</Tag>
                if (model.status === 'installed') return <Tag color="success">已安装</Tag>
                return <Tag>未安装</Tag>
              },
            },
            {
              title: '操作',
              width: 360,
              render: (_, model: LocalModel) => model.status !== 'installed' ? (
                <Button
                  type={hardware?.recommended_model === model.model_key ? 'primary' : 'default'}
                  icon={<CloudDownloadOutlined />}
                  onClick={() => install(model)}
                >
                  {hardware?.recommended_model === model.model_key ? '安装推荐模型' : '安装'}
                </Button>
              ) : (
                <Space wrap>
                  <Button disabled={!usageEnabled} icon={<PlayCircleOutlined />} onClick={() => start(model)}>加载</Button>
                  <Button disabled={!usageEnabled} icon={<ThunderboltOutlined />} onClick={() => benchmark(model)}>测速</Button>
                  <Button disabled={!usageEnabled} onClick={() => makeDefault(model)}>设为默认</Button>
                  <Tooltip title="删除模型文件，不删除作品数据">
                    <Button danger icon={<DeleteOutlined />} onClick={() => remove(model)} />
                  </Tooltip>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Card size="small" title="按任务选择本地模型">
        <Space wrap size={16}>
          {[
            ['chat', '项目助手'],
            ['cataloging', '作品建档'],
            ['planning', '大纲与新书'],
            ['writing', '章节写作'],
            ['evaluation', '质量评估'],
          ].map(([task, label]) => (
            <div key={task} style={{ minWidth: 220 }}>
              <Text type="secondary">{label}</Text>
              <Select
                allowClear
                disabled={!usageEnabled}
                style={{ width: '100%', marginTop: 4 }}
                value={catalog?.task_settings?.[task]?.model_key}
                placeholder="跟随全局默认/API/CLI"
                options={(catalog?.items || [])
                  .filter((item) => item.status === 'installed')
                  .map((item) => ({ value: item.model_key, label: item.display_name }))}
                onChange={(value) => saveTaskModel(task, value)}
              />
              {catalog?.task_settings?.[task]?.context_length ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {Math.round((catalog?.task_settings?.[task]?.context_length || 0) / 1024)}K 上下文
                </Text>
              ) : null}
            </div>
          ))}
        </Space>
      </Card>

      <Alert
        type="info"
        showIcon
        icon={<ExperimentOutlined />}
        message="任务模型是本地运行时的显式覆盖；清空后会跟随系统全局默认模型/API/CLI，不会再自动抢占建档或新书生成。"
      />
    </Space>
  )
}
