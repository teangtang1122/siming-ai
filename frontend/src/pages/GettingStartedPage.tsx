import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Collapse,
  Progress,
  Result,
  Select,
  Space,
  Steps,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  CloudDownloadOutlined,
  ExperimentOutlined,
  LinkOutlined,
  ReloadOutlined,
  RocketOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import PageWrapper from '../components/PageWrapper'
import SystemNav from '../components/SystemNav'
import './GettingStartedPage.css'

const { Paragraph, Text, Title } = Typography

interface FreeModelOption {
  id: string
  display_name: string
  recommended: boolean
}

interface GettingStartedStatus {
  installed: boolean
  command?: string | null
  version?: string | null
  managed_by_siming: boolean
  model_source: 'cli' | 'fallback' | 'none'
  free_models: FreeModelOption[]
  recommended_model?: string | null
  platform_supported: boolean
  install_location: string
  configured: boolean
  configured_model?: string | null
  is_global_default: boolean
  has_any_model: boolean
  needs_setup: boolean
  global_model?: { provider: string; model: string } | null
  official_links: {
    releases: string
    install_docs: string
    model_docs: string
  }
}

interface InstallJob {
  id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  phase: string
  percent: number
  message: string
  error?: string
  next_action?: string
  bytes_downloaded?: number
  bytes_total?: number
  command?: string
  version?: string
  sha256?: string
  free_models?: FreeModelOption[]
  recommended_model?: string
}

interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

interface ConfigureResult {
  provider: string
  model: string
  command: string
  cli_args: string
  status: GettingStartedStatus
}

const formatBytes = (bytes?: number) => {
  if (!bytes) return '0 MB'
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const errorText = (error: unknown) => error instanceof Error ? error.message : '操作没有完成，请重试'

export function GettingStartedPanel() {
  const navigate = useNavigate()
  const [status, setStatus] = useState<GettingStartedStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [installJob, setInstallJob] = useState<InstallJob | null>(null)
  const [selectedModel, setSelectedModel] = useState<string>()
  const [setupState, setSetupState] = useState<'idle' | 'testing' | 'success' | 'error'>('idle')
  const [setupError, setSetupError] = useState('')
  const [showFreeSetup, setShowFreeSetup] = useState(false)

  const fetchStatus = useCallback(async (refresh = false, summaryOnly = false) => {
    setLoading(true)
    try {
      let response = await apiClient.get<ApiEnvelope<GettingStartedStatus>>('/config/getting-started', {
        refresh,
        summary: summaryOnly,
      })
      if (summaryOnly && response.data.data.needs_setup) {
        response = await apiClient.get<ApiEnvelope<GettingStartedStatus>>('/config/getting-started', {
          refresh,
          summary: false,
        })
      }
      const next = response.data.data
      setStatus(next)
      setSelectedModel((current) => current || next.configured_model || next.recommended_model || next.free_models[0]?.id)
    } catch (error) {
      setSetupError(errorText(error))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void fetchStatus(false, true)
  }, [fetchStatus])

  useEffect(() => {
    if (!installJob || !['pending', 'running'].includes(installJob.status)) return
    const timer = window.setTimeout(async () => {
      try {
        const response = await apiClient.get<ApiEnvelope<InstallJob>>(`/config/getting-started/opencode/install/${installJob.id}`)
        const next = response.data.data
        setInstallJob(next)
        if (next.status === 'completed') {
          setSelectedModel(next.recommended_model || next.free_models?.[0]?.id)
          await fetchStatus(true)
        }
      } catch (error) {
        setInstallJob((current) => current ? { ...current, status: 'failed', error: errorText(error) } : current)
      }
    }, 1000)
    return () => window.clearTimeout(timer)
  }, [fetchStatus, installJob])

  const startInstall = async () => {
    setSetupError('')
    setSetupState('idle')
    try {
      const response = await apiClient.post<ApiEnvelope<InstallJob>>('/config/getting-started/opencode/install')
      setInstallJob(response.data.data)
    } catch (error) {
      setSetupError(errorText(error))
    }
  }

  const configureAndTest = async () => {
    if (!status?.command || !selectedModel) return
    setSetupState('testing')
    setSetupError('')
    try {
      const configured = await apiClient.post<ApiEnvelope<ConfigureResult>>('/config/getting-started/opencode/configure', {
        command: status.command,
        model: selectedModel,
      })
      await apiClient.post('/config/models/test', {
        provider: 'opencode_cli',
        cli_command: configured.data.data.command,
        cli_args: configured.data.data.cli_args,
        model: selectedModel,
        timeout_seconds: 60,
      })
      await apiClient.put('/config/global-model', {
        provider: 'opencode_cli',
        model: selectedModel,
      })
      setSetupState('success')
      message.success('免费模型已经可以使用')
      await fetchStatus(false, true)
    } catch (error) {
      setSetupState('error')
      setSetupError(errorText(error))
    }
  }

  const currentStep = useMemo(() => {
    if (setupState === 'success' || status?.is_global_default) return 2
    if (status?.installed) return 1
    return 0
  }, [setupState, status])

  if (loading && !status) {
    return <div className="getting-started-loading" role="status">正在检查这台电脑...</div>
  }

  if (!status) {
    return (
      <Alert
        type="error"
        showIcon
        message="暂时无法检查电脑环境"
        description={setupError || '请确认司命后端仍在运行。'}
        action={<Button onClick={() => void fetchStatus(true)}>重新检查</Button>}
      />
    )
  }

  const alreadyReady = !status.needs_setup && !showFreeSetup && setupState !== 'success'
  const quotaError = /free usage|quota|rate limit|额度|限额|限流/i.test(setupError)

  if (alreadyReady) {
    return (
      <Result
        status="success"
        title="司命已经可以使用 AI"
        subTitle={`当前默认模型：${status.global_model?.provider}:${status.global_model?.model}`}
        extra={[
          <Button key="assistant" type="primary" onClick={() => navigate('/gui')}>去和司命对话</Button>,
          <Button
            key="free"
            onClick={() => {
              setShowFreeSetup(true)
              void fetchStatus(true)
            }}
          >
            再添加一个免费 OpenCode 模型
          </Button>,
        ]}
      />
    )
  }

  return (
    <div className="getting-started-panel">
      <div className="getting-started-promise" aria-label="免费开始的特点">
        <span><ThunderboltOutlined /> 不用命令行</span>
        <span><SafetyCertificateOutlined /> 不用填写 API Key</span>
        <span><ToolOutlined /> 不修改系统环境变量</span>
      </div>

      <div className="getting-started-layout">
        <aside className="getting-started-steps" aria-label="设置进度">
          <Text className="getting-started-route-label">推荐路线 · 免费体验</Text>
          <Steps
            direction="vertical"
            size="small"
            current={currentStep}
            items={[
              { title: '安装 OpenCode', description: '司命自动下载和校验' },
              { title: '选择免费模型', description: '从当前可用列表中选择' },
              { title: '测试并完成', description: '成功后自动设为默认' },
            ]}
          />
        </aside>

        <section className="getting-started-work" aria-live="polite">
          {!status.installed ? (
            <>
              <Tag color="processing">第 1 步</Tag>
              <Title level={3}>让司命安装 OpenCode</Title>
              <Paragraph>
                点击一次即可。司命会从 OpenCode 官方 GitHub 下载约 70 MB 的 Windows CLI，核对官方 SHA256 后放进司命自己的目录。
                不需要安装 Node.js，不需要打开 PowerShell，也不需要管理员权限。
              </Paragraph>

              {installJob && installJob.status !== 'failed' ? (
                <div className="getting-started-progress">
                  <Progress percent={installJob.percent} status={installJob.status === 'completed' ? 'success' : 'active'} />
                  <div className="getting-started-progress-meta">
                    <Text>{installJob.message}</Text>
                    {Boolean(installJob.bytes_total) && (
                      <Text type="secondary">{formatBytes(installJob.bytes_downloaded)} / {formatBytes(installJob.bytes_total)}</Text>
                    )}
                  </div>
                </div>
              ) : (
                <Button
                  type="primary"
                  size="large"
                  icon={<CloudDownloadOutlined />}
                  disabled={!status.platform_supported}
                  onClick={() => void startInstall()}
                >
                  一键安装 OpenCode
                </Button>
              )}

              {(installJob?.status === 'failed' || setupError) && (
                <Alert
                  className="getting-started-alert"
                  type="error"
                  showIcon
                  message="自动安装没有完成"
                  description={installJob?.error || setupError}
                  action={<Button onClick={() => void startInstall()}>重试</Button>}
                />
              )}

              <div className="getting-started-trust">
                <SafetyCertificateOutlined />
                <div>
                  <Text strong>下载来源和文件校验都可以核对</Text>
                  <Text type="secondary">安装位置：{status.install_location}</Text>
                </div>
              </div>
              <Space wrap>
                <Button icon={<ReloadOutlined />} onClick={() => void fetchStatus(true)}>我已经安装，重新检测</Button>
                <Button icon={<LinkOutlined />} href={status.official_links.releases} target="_blank" rel="noreferrer">
                  打开 OpenCode 官方下载页
                </Button>
              </Space>
            </>
          ) : setupState === 'success' || status.is_global_default ? (
            <Result
              status="success"
              icon={<CheckCircleOutlined />}
              title="设置完成，可以开始体验"
              subTitle={`司命现在会优先使用 ${selectedModel || status.configured_model}。以后可以随时在“模型与 AI”中更换。`}
              extra={[
                <Button key="chat" type="primary" size="large" onClick={() => navigate('/gui')}>去和司命对话</Button>,
                <Button key="create" size="large" onClick={() => navigate('/novel-creation')}>创建第一本小说</Button>,
              ]}
            />
          ) : (
            <>
              <Tag color="processing">第 2-3 步</Tag>
              <Title level={3}>选择一个免费模型并测试</Title>
              <Paragraph>
                下面是 OpenCode 此刻返回的免费模型。推荐项已经替你选好；免费模型可能有每日限额，也可能由 OpenCode 调整，司命不会把它描述成永久免费。
              </Paragraph>
              {status.model_source === 'fallback' && (
                <Alert
                  type="warning"
                  showIcon
                  message="暂时没有读到实时模型列表"
                  description="当前显示的是司命内置候选。可以先重新检测；如果仍然失败，再打开官方模型说明。"
                />
              )}
              <label className="getting-started-model-label" htmlFor="getting-started-model">免费模型</label>
              <Select
                id="getting-started-model"
                size="large"
                value={selectedModel}
                onChange={setSelectedModel}
                className="getting-started-model-select"
                options={status.free_models.map((model) => ({
                  value: model.id,
                  label: model.recommended ? `${model.display_name}（推荐）` : model.display_name,
                }))}
                placeholder="没有发现免费模型，请重新检测"
                disabled={status.free_models.length === 0 || setupState === 'testing'}
              />
              <Space wrap>
                <Button
                  type="primary"
                  size="large"
                  icon={<ExperimentOutlined />}
                  loading={setupState === 'testing'}
                  disabled={!selectedModel}
                  onClick={() => void configureAndTest()}
                >
                  {setupState === 'testing' ? '正在测试，最长 60 秒...' : '完成设置并测试'}
                </Button>
                <Button icon={<ReloadOutlined />} onClick={() => void fetchStatus(true)}>重新检测模型</Button>
              </Space>
              {setupState === 'error' && (
                <Alert
                  className="getting-started-alert"
                  type="error"
                  showIcon
                  message={quotaError ? '免费额度暂时用完了' : '模型测试没有通过'}
                  description={quotaError
                    ? '安装和配置已经完成，只是这个免费模型当前触发了额度限制。可以换一个免费模型，或稍后再试。'
                    : setupError}
                  action={quotaError ? <Button onClick={() => setSetupState('idle')}>换一个模型</Button> : undefined}
                />
              )}
              <div className="getting-started-detected">
                <CheckCircleOutlined />
                <Text>已检测到 OpenCode {status.version || ''}</Text>
                <Text type="secondary">{status.managed_by_siming ? '由司命管理' : '使用电脑中已有的安装'}</Text>
              </div>
            </>
          )}
        </section>
      </div>

      <Alert
        className="getting-started-free-note"
        type="info"
        showIcon
        message="关于免费模型"
        description="使用云端免费模型时，小说内容会发送给对应模型服务处理；可用模型、限额和数据政策由 OpenCode 或模型提供方决定。重要或私密作品请先阅读官方说明，或改用你信任的付费 API。"
        action={<Button type="link" href={status.official_links.model_docs} target="_blank" rel="noreferrer">查看官方说明</Button>}
      />

      <Collapse
        ghost
        className="getting-started-alternatives"
        items={[{
          key: 'other',
          label: '我想使用其他模型，或手动安装',
          children: (
            <div className="getting-started-alternative-content">
              <Paragraph>
                已经有 API Key、Codex、Claude Code 或其他 CLI 时，可以继续使用原来的配置入口。熟悉命令行的用户也可以手动运行
                <Text code copyable>npm install -g opencode-ai</Text>，然后回到这里点击“重新检测”。
              </Paragraph>
              <Space wrap>
                <Button icon={<SettingOutlined />} onClick={() => navigate('/settings?section=ai')}>配置其他模型</Button>
                <Button icon={<LinkOutlined />} href={status.official_links.install_docs} target="_blank" rel="noreferrer">OpenCode 官方安装说明</Button>
              </Space>
            </div>
          ),
        }]}
      />
    </div>
  )
}

export default function GettingStartedPage() {
  const navigate = useNavigate()

  return (
    <PageWrapper maxWidth={1180} className="getting-started-page">
      <SystemNav current="getting-started" />
      <header className="siming-section-header getting-started-heading">
        <div>
          <span className="siming-section-kicker">第一次使用</span>
          <Title level={2}><RocketOutlined /> 先让司命能用起来</Title>
          <p className="siming-section-description">从一键安装免费模型开始。整个过程都在司命里完成，不要求你先学会命令行。</p>
        </div>
        <Button icon={<ArrowRightOutlined />} onClick={() => navigate('/dashboard')}>稍后设置</Button>
      </header>
      <GettingStartedPanel />
    </PageWrapper>
  )
}
