import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Progress,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
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
  FolderOpenOutlined,
  PlayCircleOutlined,
  PoweroffOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { apiClient } from '../../api/client'
import { useGlobalModelActions } from '../../shared/query/modelConfigs'
import type {
  CatalogResponse,
  DownloadTask,
  HardwareProfile,
  LocalModel,
  LocalModelQualification,
  RuntimeLaunchSettings,
} from './types'

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
  const [customModel, setCustomModel] = useState({
    modelKey: '', displayName: '', sourceUrl: '', filePath: '', contextLength: 16384,
  })
  const [qualifyingModel, setQualifyingModel] = useState<string | null>(null)
  const [qualification, setQualification] = useState<LocalModelQualification | null>(null)
  const [settingsModel, setSettingsModel] = useState<LocalModel | null>(null)
  const [launchSettings, setLaunchSettings] = useState<RuntimeLaunchSettings | null>(null)
  const [settingsSaved, setSettingsSaved] = useState(false)
  const [settingsBusy, setSettingsBusy] = useState(false)
  const [usageBusy, setUsageBusy] = useState(false)
  const [cancelingTaskId, setCancelingTaskId] = useState<string | null>(null)
  const { setGlobalModel } = useGlobalModelActions()
  const usageEnabled = catalog?.usage_enabled !== false
  const usageDisabledReason = catalog?.usage_disabled_reason || '本地模型已关闭。开启后可以继续使用。'

  const contextForModel = (modelKey?: string | null) => {
    const capacity = catalog?.items.find((item) => item.model_key === modelKey)?.context_length
    const recommended = hardware?.recommended_context || 16384
    return capacity ? Math.min(capacity, recommended) : recommended
  }

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

  const pickModelRoot = async () => {
    try {
      const response = await apiClient.post<{ data: { path?: string | null; cancelled?: boolean } }>('/local-models/root/pick')
      const path = response.data.data.path
      if (path) setModelRoot(path)
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const pickCustomModel = async () => {
    try {
      const response = await apiClient.post<{ data: { path?: string | null; cancelled?: boolean } }>('/local-models/custom/pick')
      const path = response.data.data.path
      if (path) {
        const filename = path.split(/[\\/]/).pop() || ''
        setCustomModel((current) => ({
          ...current,
          filePath: path,
          modelKey: current.modelKey || filename.replace(/\.gguf$/i, '').replace(/[^A-Za-z0-9_.-]+/g, '-'),
          displayName: current.displayName || filename.replace(/\.gguf$/i, ''),
        }))
      }
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const resumeDownload = async (taskId: string) => {
    try {
      await apiClient.post(`/local-models/downloads/${taskId}/resume`)
      message.success('已从保存的进度继续下载')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const cancelDownload = async (taskId: string) => {
    setCancelingTaskId(taskId)
    try {
      await apiClient.post(`/local-models/downloads/${taskId}/cancel`)
      message.success('下载已取消；临时文件保留，后续可重新下载')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    } finally {
      setCancelingTaskId(null)
    }
  }

  const setUsage = async (enabled: boolean) => {
    setUsageBusy(true)
    try {
      await apiClient.put('/local-models/runtime/usage', { enabled })
      message.success(enabled ? '本地模型已开启' : '本地模型已关闭，运行中的模型已停止')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    } finally {
      setUsageBusy(false)
    }
  }

  const pickAndRegisterModel = async (model: LocalModel) => {
    try {
      const picked = await apiClient.post<{ data: { path?: string | null } }>('/local-models/custom/pick')
      const path = picked.data.data.path
      if (!path) return
      const hide = message.loading('正在校验 GGUF 文件...', 0)
      try {
        await apiClient.post(`/local-models/catalog/${model.model_key}/register-file`, { file_path: path })
        message.success('已登记现有模型文件，无需重新下载')
        await onRefresh()
      } finally {
        hide()
      }
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const pickAndRegisterRuntime = async () => {
    try {
      const picked = await apiClient.post<{ data: { path?: string | null } }>('/local-models/runtime/pick')
      const path = picked.data.data.path
      if (!path) return
      await apiClient.post('/local-models/runtime/register-file', { file_path: path })
      message.success('已登记本机 llama-server.exe')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const installRuntime = async () => {
    try {
      await apiClient.post('/local-models/runtime/install')
      message.success('运行时下载已创建')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const openSettings = async (model: LocalModel) => {
    setSettingsModel(model)
    setLaunchSettings(null)
    try {
      const response = await apiClient.get<{ data: { profile: RuntimeLaunchSettings; saved: boolean } }>(
        `/local-models/runtime/settings/${model.model_key}`,
      )
      setLaunchSettings(response.data.data.profile)
      setSettingsSaved(response.data.data.saved)
    } catch (error: any) {
      message.error(error.message)
      setSettingsModel(null)
    }
  }

  const changeSetting = <K extends keyof RuntimeLaunchSettings>(key: K, value: RuntimeLaunchSettings[K]) => {
    setLaunchSettings((current) => current ? { ...current, [key]: value } : null)
  }

  const restoreSettings = async () => {
    if (!settingsModel) return
    setSettingsBusy(true)
    try {
      const response = await apiClient.delete<{ data: { profile: RuntimeLaunchSettings; saved: boolean } }>(
        `/local-models/runtime/settings/${settingsModel.model_key}`,
      )
      setLaunchSettings(response.data.data.profile)
      setSettingsSaved(false)
      message.success('已恢复默认启动参数；下次启动生效')
    } catch (error: any) {
      message.error(error.message)
    } finally {
      setSettingsBusy(false)
    }
  }

  const saveSettings = async (startAfterSave: boolean) => {
    if (!settingsModel || !launchSettings) return
    setSettingsBusy(true)
    try {
      await apiClient.put(`/local-models/runtime/settings/${settingsModel.model_key}`, launchSettings)
      setSettingsSaved(true)
      if (startAfterSave) {
        const started = await start(settingsModel)
        if (started) setSettingsModel(null)
      } else {
        message.success('启动参数已保存；下次启动生效')
      }
    } catch (error: any) {
      message.error(error.message)
    } finally {
      setSettingsBusy(false)
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

  const start = async (model: LocalModel): Promise<boolean> => {
    try {
      await apiClient.post('/local-models/runtime/start', {
        model_key: model.model_key,
        task_type: 'assistant',
      })
      message.success('本地模型已加载')
      await onRefresh()
      return true
    } catch (error: any) {
      message.error(error.message)
      return false
    }
  }

  const stop = async () => {
    try {
      await apiClient.post('/local-models/runtime/stop')
      message.success('本地模型已停止；本地模型开关仍保持开启')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const remove = async (model: LocalModel) => {
    await apiClient.delete(`/local-models/${model.model_key}`)
    message.success('模型已从目录移除')
    await onRefresh()
  }

  const makeDefault = async (model: LocalModel) => {
    await setGlobalModel('local_llama_cpp', model.model_key)
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
      const outputKind = result.reasoning_only ? '（模型仅返回思考内容，本次仍计入测速）' : ''
      message.success(
        result.tokens_per_second
          ? `${result.tokens_estimated ? '约 ' : ''}${result.tokens_per_second} token/s，用时 ${result.elapsed_seconds}s${outputKind}`
          : `测速完成，用时 ${result.elapsed_seconds}s${outputKind}`,
      )
    } catch (error: any) {
      message.error(error.message)
    } finally {
      hide()
    }
  }

  const qualify = async (model: LocalModel) => {
    setQualifyingModel(model.model_key)
    const hide = message.loading('正在执行真实任务验证，可能需要几分钟…', 0)
    try {
      const response = await apiClient.post<{ data: LocalModelQualification }>('/local-models/qualify', {
        model_key: model.model_key,
        context_length: contextForModel(model.model_key),
      })
      setQualification(response.data.data)
    } catch (error: any) {
      message.error(error.message)
    } finally {
      hide()
      setQualifyingModel(null)
    }
  }

  const downloadCustomModel = async () => {
    try {
      await apiClient.post('/local-models/custom/download', {
        model_key: customModel.modelKey,
        display_name: customModel.displayName,
        source_url: customModel.sourceUrl,
        context_length: customModel.contextLength,
      })
      message.success('自有 GGUF 下载已加入任务中心')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const importCustomModel = async () => {
    try {
      await apiClient.post('/local-models/custom/import', {
        model_key: customModel.modelKey,
        display_name: customModel.displayName,
        file_path: customModel.filePath,
        context_length: customModel.contextLength,
      })
      message.success('自有 GGUF 已登记，可立即加载')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const visibleDownloads = downloads.filter((item) => !['completed', 'cancelled'].includes(item.status))

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        size="small"
        title="本地模型运行"
        extra={<Space><Text>允许使用本地模型</Text><Switch checked={usageEnabled} loading={usageBusy} onChange={setUsage} /></Space>}
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space wrap>
            <Tag color={catalog?.runtime.running ? 'processing' : 'default'}>
              {catalog?.runtime.running ? `运行中 · ${catalog.runtime.model_key}` : '未运行'}
            </Tag>
            {catalog?.runtime.context_length && <Text type="secondary">上下文 {Math.round(catalog.runtime.context_length / 1024)}K</Text>}
            {catalog?.runtime.running && <Button icon={<PoweroffOutlined />} onClick={stop}>停止当前模型</Button>}
          </Space>
          <Text type="secondary">停止当前模型后，下次使用可能自动启动。关闭右侧开关可阻止自动启动，并停止当前进程。</Text>
          <Space wrap>
            <Text type="secondary">运行时：{catalog?.runtime.executable_path || '尚未安装 llama.cpp'}</Text>
            <Button size="small" icon={<FolderOpenOutlined />} onClick={pickAndRegisterRuntime}>使用本机 llama-server.exe</Button>
            {!catalog?.runtime.executable_path && <Button size="small" onClick={installRuntime}>下载运行时</Button>}
          </Space>
        </Space>
      </Card>
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
              {hardware.recommended_model ? (
                <><Tag color="blue">{hardware.recommended_model}</Tag>{hardware.recommended_context / 1024}K 上下文</>
              ) : '当前硬件不建议运行 27B'}
            </Descriptions.Item>
          </Descriptions>
          {!hardware.training_supported && (
            <Alert
              style={{ marginTop: 12 }}
              type="info"
              showIcon
              message="27B QLoRA 训练需要至少 24GB 显存的 NVIDIA 显卡。本地推理仍可单独尝试。"
            />
          )}
        </Card>
      )}

      <Card size="small" title="模型存储目录">
        <Space.Compact style={{ width: '100%' }}>
          <Input value={modelRoot} onChange={(event) => setModelRoot(event.target.value)} />
          <Button icon={<FolderOpenOutlined />} onClick={pickModelRoot}>选择文件夹</Button>
          <Button onClick={saveModelRoot}>保存</Button>
        </Space.Compact>
      </Card>

      <Card
        size="small"
        title="自有 GGUF 模型"
        extra={<Text type="secondary">不受内置目录限制</Text>}
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Text type="secondary">
            可以直接登记已下载的 GGUF，或给出其直链下载地址。模型来源、许可证和上下文能力由你确认；司命不会把它复制或移动。
          </Text>
          <Space wrap style={{ width: '100%' }}>
            <Input
              aria-label="自有模型标识"
              placeholder="模型标识，例如 qwen36-27b-q4"
              value={customModel.modelKey}
              onChange={(event) => setCustomModel((current) => ({ ...current, modelKey: event.target.value }))}
              style={{ width: 230 }}
            />
            <Input
              aria-label="自有模型名称"
              placeholder="显示名称"
              value={customModel.displayName}
              onChange={(event) => setCustomModel((current) => ({ ...current, displayName: event.target.value }))}
              style={{ width: 220 }}
            />
            <InputNumber
              aria-label="自有模型上下文"
              min={1}
              controls
              value={customModel.contextLength}
              onChange={(value) => setCustomModel((current) => ({ ...current, contextLength: Number(value) || 1 }))}
              addonAfter="tokens"
              style={{ width: 200 }}
            />
          </Space>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              aria-label="GGUF 下载地址"
              placeholder="https://…/model.gguf（直接下载）"
              value={customModel.sourceUrl}
              onChange={(event) => setCustomModel((current) => ({ ...current, sourceUrl: event.target.value }))}
            />
            <Button type="primary" onClick={downloadCustomModel}>下载并登记</Button>
          </Space.Compact>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              aria-label="本机 GGUF 路径"
              placeholder="D:\\Models\\model.gguf（直接登记，不复制）"
              value={customModel.filePath}
              onChange={(event) => setCustomModel((current) => ({ ...current, filePath: event.target.value }))}
            />
            <Button icon={<FolderOpenOutlined />} onClick={pickCustomModel}>选择 GGUF</Button>
            <Button onClick={importCustomModel}>登记本机文件</Button>
          </Space.Compact>
        </Space>
      </Card>

      {visibleDownloads.length > 0 && (
        <Card size="small" title="下载进度">
          <Space direction="vertical" style={{ width: '100%' }}>
            {visibleDownloads.map((task) => {
              const percent = task.total_bytes
                ? Math.min(100, Math.round(task.downloaded_bytes / task.total_bytes * 100))
                : 0
              const canResume = task.kind === 'runtime' || catalog?.items.some((model) => model.model_key === task.target_key)
              return (
                <div key={task.id}>
                  <Space style={{ marginBottom: 4 }}>
                    <Text strong>{task.target_key}</Text>
                    <Tag>{task.kind === 'runtime' ? '运行时' : '模型'}</Tag>
                    <Text type="secondary">
                      {formatBytes(task.downloaded_bytes)} / {formatBytes(task.total_bytes)}
                    </Text>
                  </Space>
                  <Space.Compact style={{ width: '100%' }}>
                    <Progress percent={percent} status={task.status === 'failed' ? 'exception' : 'active'} />
                    {task.status === 'failed' && canResume && (
                      <Button disabled={cancelingTaskId === task.id} onClick={() => resumeDownload(task.id)}>重试并续传</Button>
                    )}
                    <Button loading={cancelingTaskId === task.id} onClick={() => cancelDownload(task.id)}>
                      {task.status === 'failed' ? '移除记录' : '取消下载'}
                    </Button>
                  </Space.Compact>
                  {task.status === 'failed' && !canResume && (
                    <Text type="warning">该模型已退出内置目录，请移除旧记录。</Text>
                  )}
                  {task.error_message && canResume && (
                    <Text type={task.status === 'failed' ? 'danger' : 'warning'}>{task.error_message}</Text>
                  )}
                </div>
              )
            })}
          </Space>
        </Card>
      )}

      <Card
        size="small"
        title="模型目录"
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
              render: (_, model: LocalModel) =>
                model.recommended_vram_gb != null
                  ? `${model.recommended_vram_gb}GB 显存`
                  : '由用户确认',
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
              width: 450,
              render: (_, model: LocalModel) => model.status !== 'installed' ? (
                <Space wrap>
                  <Button
                    type={hardware?.recommended_model === model.model_key ? 'primary' : 'default'}
                    icon={<CloudDownloadOutlined />}
                    onClick={() => install(model)}
                  >
                    {hardware?.recommended_model === model.model_key ? '安装推荐模型' : '安装'}
                  </Button>
                  {model.source === 'catalog' && (
                    <Button icon={<FolderOpenOutlined />} onClick={() => pickAndRegisterModel(model)}>登记已有 GGUF</Button>
                  )}
                </Space>
              ) : (
                <Space wrap>
                  <Button disabled={!usageEnabled} icon={<PlayCircleOutlined />} onClick={() => start(model)}>启动</Button>
                  <Button onClick={() => openSettings(model)}>启动参数</Button>
                  <Button disabled={!usageEnabled} icon={<ThunderboltOutlined />} onClick={() => benchmark(model)}>测速</Button>
                  <Button
                    disabled={!usageEnabled}
                    icon={<ExperimentOutlined />}
                    loading={qualifyingModel === model.model_key}
                    onClick={() => qualify(model)}
                  >
                    任务验证
                  </Button>
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

      <Modal
        open={Boolean(settingsModel)}
        title={`${settingsModel?.display_name || ''} · 启动参数`}
        width={760}
        onCancel={() => setSettingsModel(null)}
        footer={[
          <Button key="restore" disabled={!settingsSaved} loading={settingsBusy} onClick={restoreSettings}>恢复默认</Button>,
          <Button key="save" disabled={!launchSettings} loading={settingsBusy} onClick={() => saveSettings(false)}>保存</Button>,
          <Button key="start" type="primary" disabled={!usageEnabled || !launchSettings} loading={settingsBusy} onClick={() => saveSettings(true)}>保存并启动</Button>,
        ]}
      >
        {!launchSettings ? <Text type="secondary">正在读取启动参数…</Text> : (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert type="info" showIcon message={
              settingsModel?.model_key === 'qwen3.8-27b-q3'
                ? '本机测试参数：32K 上下文、Q4 KV 缓存、MTP 2、Flash Attention；16GB 显存下 96K 会明显变慢。'
                : '修改会在下一次启动时生效；保存并启动会按新参数重新加载。'
            } />
            <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
              <Descriptions.Item label="上下文 tokens">
                <InputNumber aria-label="启动上下文" min={1024} step={1024} value={launchSettings.context_length} onChange={(value) => changeSetting('context_length', value || 1024)} style={{ width: '100%' }} />
              </Descriptions.Item>
              <Descriptions.Item label="GPU 层数（0=CPU）">
                <InputNumber aria-label="GPU 层数" min={0} max={999} value={launchSettings.gpu_layers} onChange={(value) => changeSetting('gpu_layers', value ?? 0)} style={{ width: '100%' }} />
              </Descriptions.Item>
              <Descriptions.Item label="CPU 线程">
                <InputNumber aria-label="CPU 线程" min={1} max={256} value={launchSettings.threads} onChange={(value) => changeSetting('threads', value)} style={{ width: '100%' }} />
              </Descriptions.Item>
              <Descriptions.Item label="Flash Attention">
                <Select aria-label="Flash Attention" value={launchSettings.flash_attention} onChange={(value) => changeSetting('flash_attention', value)} options={[{ value: 'auto', label: '自动' }, { value: 'on', label: '开启' }, { value: 'off', label: '关闭' }]} style={{ width: '100%' }} />
              </Descriptions.Item>
              <Descriptions.Item label="显存适配 fit">
                <Select aria-label="显存适配" value={launchSettings.fit} onChange={(value) => changeSetting('fit', value)} options={[{ value: 'auto', label: '自动' }, { value: 'on', label: '开启' }, { value: 'off', label: '关闭' }]} style={{ width: '100%' }} />
              </Descriptions.Item>
              <Descriptions.Item label="KV 缓存精度">
                <Select aria-label="KV 缓存精度" value={launchSettings.kv_cache_type} onChange={(value) => changeSetting('kv_cache_type', value)} options={[{ value: 'f16', label: 'F16' }, { value: 'q8_0', label: 'Q8_0' }, { value: 'q4_0', label: 'Q4_0' }]} style={{ width: '100%' }} />
              </Descriptions.Item>
              <Descriptions.Item label="MTP 预测 tokens">
                <InputNumber aria-label="MTP 预测 tokens" min={0} max={4} value={launchSettings.mtp_draft_tokens} onChange={(value) => changeSetting('mtp_draft_tokens', value ?? 0)} style={{ width: '100%' }} />
              </Descriptions.Item>
              <Descriptions.Item label="内存缓存 MB">
                <InputNumber aria-label="内存缓存 MB" min={0} max={65536} step={512} value={launchSettings.cache_ram_mb} onChange={(value) => changeSetting('cache_ram_mb', value ?? 0)} style={{ width: '100%' }} />
              </Descriptions.Item>
              <Descriptions.Item label="思考强度">
                <Select aria-label="思考强度" value={launchSettings.reasoning_effort} onChange={(value) => changeSetting('reasoning_effort', value)} options={[{ value: null, label: '模型默认' }, { value: 'low', label: '低' }, { value: 'medium', label: '中' }, { value: 'high', label: '高' }]} style={{ width: '100%' }} />
              </Descriptions.Item>
              <Descriptions.Item label="温度">
                <InputNumber aria-label="温度" min={0} max={2} step={0.05} value={launchSettings.temperature} onChange={(value) => changeSetting('temperature', value)} style={{ width: '100%' }} />
              </Descriptions.Item>
              <Descriptions.Item label="Top P">
                <InputNumber aria-label="Top P" min={0} max={1} step={0.05} value={launchSettings.top_p} onChange={(value) => changeSetting('top_p', value)} style={{ width: '100%' }} />
              </Descriptions.Item>
              <Descriptions.Item label="Top K">
                <InputNumber aria-label="Top K" min={0} max={200} value={launchSettings.top_k} onChange={(value) => changeSetting('top_k', value)} style={{ width: '100%' }} />
              </Descriptions.Item>
              <Descriptions.Item label="Min P">
                <InputNumber aria-label="Min P" min={0} max={1} step={0.05} value={launchSettings.min_p} onChange={(value) => changeSetting('min_p', value)} style={{ width: '100%' }} />
              </Descriptions.Item>
              <Descriptions.Item label="重复惩罚">
                <InputNumber aria-label="重复惩罚" min={0.5} max={2} step={0.05} value={launchSettings.repeat_penalty} onChange={(value) => changeSetting('repeat_penalty', value)} style={{ width: '100%' }} />
              </Descriptions.Item>
            </Descriptions>
          </Space>
        )}
      </Modal>

      <Modal
        open={Boolean(qualification)}
        title="本地模型任务验证"
        width={720}
        footer={<Button type="primary" onClick={() => setQualification(null)}>知道了</Button>}
        onCancel={() => setQualification(null)}
      >
        {qualification && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Alert
              showIcon
              type={qualification.passed ? 'success' : qualification.rating === 'limited' ? 'warning' : 'error'}
              message={qualification.passed ? '当前上下文可完成关键任务' : '当前模型或上下文仅部分合格'}
              description={
                `通过 ${qualification.passed_count}/${qualification.total_count} 项；` +
                `使用 ${Math.round(qualification.context_length / 1024)}K 上下文，耗时 ${qualification.elapsed_seconds} 秒。`
              }
            />
            {qualification.cases.map((item) => (
              <Card
                key={item.id}
                size="small"
                title={item.label}
                extra={<Tag color={item.passed ? 'success' : 'error'}>{item.passed ? '通过' : '未通过'}</Tag>}
              >
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Text>{item.detail}</Text>
                  <Text type="secondary">
                    输入 {item.input_characters.toLocaleString()} 字符 · {item.elapsed_seconds} 秒
                  </Text>
                  {item.output_preview && (
                    <Typography.Paragraph
                      code
                      ellipsis={{ rows: 3, expandable: true, symbol: '展开模型输出' }}
                      style={{ marginBottom: 0 }}
                    >
                      {item.output_preview}
                    </Typography.Paragraph>
                  )}
                </Space>
              </Card>
            ))}
          </Space>
        )}
      </Modal>
    </Space>
  )
}
