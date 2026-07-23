import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Collapse,
  Input,
  Progress,
  Select,
  Space,
  Spin,
  Steps,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  ReloadOutlined,
  RocketOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import {
  getGettingStartedStatus,
  onboardingKeys,
  useGettingStartedStatus,
} from '../features/onboarding'
import PageWrapper from '../components/PageWrapper'
import SystemNav from '../components/SystemNav'
import {
  startNovelCreationConceptRun,
  startNovelCreationSession,
  workbenchUrl,
} from '../hooks/useNovelCreationInterviewController'
import './GettingStartedPage.css'

const { Paragraph, Text, Title } = Typography
const { TextArea } = Input

interface FreeModelOption {
  id: string
  display_name: string
  recommended: boolean
  test_status?: 'untested' | 'testing' | 'ready' | 'rate_limited' | 'failed'
  failure_kind?: string | null
}

type ActivationStatus = 'pending' | 'running' | 'auth_required' | 'ready' | 'failed'
type ActivationPhase = 'checking' | 'downloading' | 'verifying' | 'auth_required' | 'authenticating' | 'credential_required' | 'discovering_models' | 'testing' | 'ready' | 'failed'

interface ActivationJob {
  id: string
  status: ActivationStatus
  phase: ActivationPhase
  percent: number
  message: string
  error?: string | null
  failure_kind?: string | null
  next_action?: string | null
  selected_model?: string | null
  preferred_model?: string | null
  free_models: FreeModelOption[]
  bytes_downloaded?: number
  bytes_total?: number
  estimated_seconds_remaining?: number | null
  auth_url?: string
  auth_mode?: 'browser' | 'credential' | null
  auth_status?: 'running' | 'credential_required' | 'submitted' | 'completed' | 'failed' | 'interrupted' | null
  auth_prompt?: string | null
}

interface GettingStartedStatus {
  free_models: FreeModelOption[]
  recommended_model?: string | null
  platform_supported: boolean
  configured: boolean
  configured_model?: string | null
  is_global_default: boolean
  needs_setup: boolean
  has_detected_models: boolean
  has_usable_models: boolean
  recommended_action?: string
  global_model?: { provider: string; model: string } | null
  activation_job?: ActivationJob | null
  official_links?: { model_docs?: string }
}

interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

const formatBytes = (bytes?: number) => {
  if (!bytes) return '0 MB'
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const errorText = (error: unknown) => error instanceof Error ? error.message : '操作没有完成，请重试'

function FirstIdea({ modelReady, model }: { modelReady: boolean; model?: string }) {
  const navigate = useNavigate()
  const [idea, setIdea] = useState('')
  const [creating, setCreating] = useState(false)

  const createIdeas = async () => {
    const brief = idea.trim()
    if (!brief) return
    setCreating(true)
    try {
      const session = await startNovelCreationSession({ userBrief: brief, mode: 'internal_llm' })
      const run = await startNovelCreationConceptRun(session.id, model)
      navigate(workbenchUrl(session.id, run.id, model))
    } catch (error) {
      message.error(errorText(error))
      setCreating(false)
    }
  }

  return (
    <div className="getting-started-first-idea">
      <CheckCircleOutlined className="getting-started-ready-icon" />
      <Title level={3}>免费写作能力已经准备好</Title>
      <Paragraph>不用先学设置。说一句你想写的故事，司命会立即给你三套书名、核心卖点、主角目标和开篇钩子。</Paragraph>
      <label htmlFor="getting-started-idea">你想写什么故事？</label>
      <TextArea
        id="getting-started-idea"
        value={idea}
        onChange={(event) => setIdea(event.target.value)}
        placeholder="例如：一个能看见他人寿命的女孩，在修仙世界经营一家只在午夜营业的客栈"
        autoSize={{ minRows: 3, maxRows: 6 }}
        maxLength={2000}
        disabled={creating || !modelReady}
      />
      <Space wrap>
        <Button
          type="primary"
          size="large"
          icon={<RocketOutlined />}
          loading={creating}
          disabled={!idea.trim() || !modelReady}
          onClick={() => void createIdeas()}
        >
          生成三套小说创意
        </Button>
        <Button onClick={() => navigate('/dashboard')}>先看看作品库</Button>
      </Space>
      <Text type="secondary">当前使用的完整模型 ID 会显示在任务记录中；若免费模型发生切换，司命会明确记录。</Text>
    </div>
  )
}

export function GettingStartedPanel() {
  const queryClient = useQueryClient()
  const statusQuery = useGettingStartedStatus()
  const status = statusQuery.data as GettingStartedStatus | undefined
  const loading = statusQuery.isLoading
  const [job, setJob] = useState<ActivationJob | null>(null)
  const [selectedModel, setSelectedModel] = useState<string>()
  const [setupError, setSetupError] = useState('')
  const [authCredential, setAuthCredential] = useState('')

  const fetchStatus = useCallback(async (refresh = false) => {
    try {
      const next = await getGettingStartedStatus(false, refresh) as GettingStartedStatus
      queryClient.setQueryData(onboardingKeys.detail(), next)
      void queryClient.invalidateQueries({ queryKey: onboardingKeys.summary() })
      if (next.activation_job && next.activation_job.status !== 'ready') setJob(next.activation_job)
      setSelectedModel((current) => current || next.recommended_model || next.free_models?.[0]?.id)
    } catch (error) {
      setSetupError(errorText(error))
    }
  }, [queryClient])

  useEffect(() => {
    if (!status) return
    const activationJob = status.activation_job || null
    if (activationJob && activationJob.status !== 'ready') setJob(activationJob)
    setSelectedModel((current) => current || status.recommended_model || status.free_models?.[0]?.id)
  }, [status])

  useEffect(() => {
    const authRunning = ['running', 'submitted'].includes(job?.auth_status || '')
    if (!job || (!['pending', 'running'].includes(job.status) && !authRunning)) return
    const timer = window.setTimeout(async () => {
      try {
        const response = await apiClient.get<ApiEnvelope<ActivationJob>>(`/config/getting-started/opencode/jobs/${job.id}`)
        const next = response.data.data
        setJob(next)
        if (next.status === 'ready') {
          localStorage.removeItem('siming_getting_started_deferred')
          await fetchStatus(false)
        }
      } catch (error) {
        setSetupError(errorText(error))
      }
    }, 1000)
    return () => window.clearTimeout(timer)
  }, [fetchStatus, job])

  const startActivation = async () => {
    setSetupError('')
    try {
      const response = await apiClient.post<ApiEnvelope<ActivationJob>>('/config/getting-started/opencode/activate', {
        preferred_model: selectedModel || null,
      })
      setJob(response.data.data)
    } catch (error) {
      setSetupError(errorText(error))
    }
  }

  const retryActivation = async () => {
    if (!job) return startActivation()
    setSetupError('')
    try {
      const response = await apiClient.post<ApiEnvelope<ActivationJob>>(`/config/getting-started/opencode/jobs/${job.id}/retry`)
      setJob(response.data.data)
    } catch (error) {
      setSetupError(errorText(error))
    }
  }

  const openAuthentication = async () => {
    if (!job) return
    try {
      const response = await apiClient.post<ApiEnvelope<ActivationJob>>(`/config/getting-started/opencode/jobs/${job.id}/authenticate`)
      setJob(response.data.data)
      message.info('官方登录已经启动，浏览器打开后完成登录即可。')
    } catch (error) {
      setSetupError(errorText(error))
    }
  }

  const submitAuthCredential = async () => {
    if (!job || !authCredential.trim()) return
    try {
      const response = await apiClient.post<ApiEnvelope<ActivationJob>>(
        `/config/getting-started/opencode/jobs/${job.id}/credential`,
        { credential: authCredential },
      )
      setAuthCredential('')
      setJob(response.data.data)
    } catch (error) {
      setSetupError(errorText(error))
    }
  }

  const currentStep = useMemo(() => {
    if (job?.status === 'ready' || status?.is_global_default) return 2
    if (job && ['discovering_models', 'testing', 'auth_required', 'authenticating', 'credential_required'].includes(job.phase)) return 1
    return 0
  }, [job, status])

  if (loading && !status) return <div className="getting-started-loading" role="status">正在检查这台电脑...</div>
  if (!status) {
    return <Alert type="error" showIcon message="暂时无法检查电脑环境" description={setupError || (statusQuery.error instanceof Error ? statusQuery.error.message : '请确认司命仍在运行。')} action={<Button onClick={() => void fetchStatus(true)}>重新检查</Button>} />
  }

  const ready = job?.status === 'ready' || (status.is_global_default && status.has_usable_models !== false)
  const activeModel = status.global_model
    ? `${status.global_model.provider}:${status.global_model.model}`
    : job?.selected_model
      ? `opencode_cli:${job.selected_model}`
      : undefined
  if (ready) return <FirstIdea modelReady model={activeModel} />

  const running = Boolean(job && ['pending', 'running'].includes(job.status))
  const downloaded = formatBytes(job?.bytes_downloaded)
  const total = formatBytes(job?.bytes_total)
  const remainingMinutes = job?.estimated_seconds_remaining
    ? Math.max(1, Math.ceil(job.estimated_seconds_remaining / 60))
    : null
  const retryLabel = job?.failure_kind === 'network'
    ? '继续下载'
    : job?.failure_kind === 'download_rate_limit'
      ? '稍后继续下载'
    : job?.failure_kind === 'certificate_verification'
      ? '重新验证连接'
      : job?.failure_kind === 'disk_space'
        ? '释放空间后重试'
        : job?.failure_kind === 'permission_or_antivirus'
          ? '允许后重试'
          : job?.failure_kind === 'quota_or_rate_limit'
            ? '重新检测免费模型'
            : '重试'
  const quotaLimited = job?.failure_kind === 'quota_or_rate_limit'
  const modelTestResults = job?.free_models?.filter((model) => model.test_status && model.test_status !== 'untested') || []
  const modelStatusLabel = (model: FreeModelOption) => {
    if (model.test_status === 'ready') return '可用'
    if (model.test_status === 'testing') return '正在测试'
    if (model.test_status === 'rate_limited') return '第三方限流'
    if (model.test_status === 'failed') return '不可用'
    return '未测试'
  }
  const credentialRequired = job?.auth_status === 'credential_required' || job?.phase === 'credential_required'
  const authenticationActive = ['running', 'submitted'].includes(job?.auth_status || '')

  return (
    <div className="getting-started-panel">
      <div className="getting-started-promise" aria-label="免费开始的特点">
        <span><ThunderboltOutlined /> 无需安装开发工具</span>
        <span><SafetyCertificateOutlined /> 无需打开命令行</span>
        <span><ToolOutlined /> 每一步都能看到进度</span>
      </div>

      <div className="getting-started-layout">
        <aside className="getting-started-steps" aria-label="设置进度">
          <Text className="getting-started-route-label">免费体验</Text>
          <Steps direction="vertical" size="small" current={currentStep} items={[
            { title: '准备写作能力', description: '自动下载并校验' },
            { title: '自动选择模型', description: '逐个测试当前免费模型' },
            { title: '生成小说创意', description: '只需说一句故事想法' },
          ]} />
        </aside>

        <section className="getting-started-work" aria-live="polite">
          <Title level={3}>从一句故事想法开始</Title>
          <Paragraph>司命会先为这台电脑准备写作所需的 AI。准备好后，你可以马上生成三套小说方向。</Paragraph>

          {running ? (
            <div className="getting-started-progress">
              {Boolean(job?.bytes_total)
                ? <Progress percent={job?.percent || 0} status="active" />
                : <div className="getting-started-indeterminate"><Spin /><Text>正在执行当前步骤，不估算虚假百分比</Text></div>}
              <div className="getting-started-progress-meta">
                <Text>{job?.message || '正在准备...'}</Text>
                {Boolean(job?.bytes_total) && <Text type="secondary">{downloaded} / {total}</Text>}
                {remainingMinutes && <Text type="secondary">预计还需约 {remainingMinutes} 分钟</Text>}
              </div>
            </div>
          ) : (
            <Button
              type="primary"
              size="large"
              icon={<RocketOutlined />}
              disabled={!status.platform_supported}
              onClick={() => void startActivation()}
            >
              准备 AI 并开始构思
            </Button>
          )}

          {(job?.status === 'auth_required' || authenticationActive) && (
            <Alert
              className="getting-started-alert"
              type="info"
              showIcon
              message={credentialRequired ? '请输入官方页面给出的一次性凭据' : authenticationActive ? '正在等待官方登录完成' : '还差一次免费的官方登录'}
              description={job?.auth_prompt || job?.next_action || '不需要购买，也不需要在司命中保存 API Key。登录完成后会自动继续检测。'}
              action={credentialRequired ? (
                <Space.Compact>
                  <Input.Password
                    value={authCredential}
                    onChange={(event) => setAuthCredential(event.target.value)}
                    placeholder="一次性验证码或令牌"
                    aria-label="OpenCode 一次性验证码或令牌"
                    onPressEnter={() => void submitAuthCredential()}
                  />
                  <Button type="primary" disabled={!authCredential.trim()} onClick={() => void submitAuthCredential()}>提交</Button>
                </Space.Compact>
              ) : (
                <Space wrap>
                  {!authenticationActive && job?.auth_status !== 'failed' && <Button type="primary" onClick={() => void openAuthentication()}>开始官方登录</Button>}
                  {job?.auth_url && <Button href={job.auth_url} target="_blank">打开登录地址</Button>}
                  {job?.auth_status === 'failed' && <Button onClick={() => void openAuthentication()}>重新登录</Button>}
                </Space>
              )}
            />
          )}

          {(job?.status === 'failed' || setupError) && (
            <Alert
              className="getting-started-alert"
              type={quotaLimited ? 'warning' : 'error'}
              showIcon
              message={quotaLimited
                ? 'OpenCode 免费服务已限流（不是网络故障）'
                : job?.failure_kind === 'download_rate_limit'
                  ? 'OpenCode 下载服务暂时限流'
                : job?.failure_kind === 'certificate_verification'
                  ? 'Windows 证书验证没有完成'
                  : '这次没有准备完成'}
              description={job?.next_action || '司命会保留下载进度，可以直接重试。'}
              action={quotaLimited ? (
                <Space wrap>
                  <Button type="primary" onClick={() => void openAuthentication()}>登录后验证个人免费额度</Button>
                  <Button icon={<ReloadOutlined />} onClick={() => void retryActivation()}>{retryLabel}</Button>
                  {status.official_links?.model_docs && <Button href={status.official_links.model_docs} target="_blank">查看官方免费模型说明</Button>}
                </Space>
              ) : <Button icon={<ReloadOutlined />} onClick={() => void retryActivation()}>{retryLabel}</Button>}
            />
          )}

          {modelTestResults.length > 0 && (
            <Collapse ghost items={[{
              key: 'model-tests',
              label: `免费模型检测结果（${modelTestResults.length}/${job?.free_models.length || 0}）`,
              children: (
                <Space wrap>
                  {modelTestResults.map((model) => (
                    <Tag
                      key={model.id}
                      color={model.test_status === 'ready' ? 'success' : model.test_status === 'testing' ? 'processing' : model.test_status === 'rate_limited' ? 'warning' : 'error'}
                    >
                      {model.display_name}：{modelStatusLabel(model)}
                    </Tag>
                  ))}
                </Space>
              ),
            }]} />
          )}

          {(job?.error || setupError) && (
            <Collapse ghost items={[{ key: 'technical', label: '查看技术详情', children: <Text type="secondary" copyable>{job?.error || setupError}</Text> }]} />
          )}
        </section>
      </div>

      <Alert
        className="getting-started-free-note"
        type="info"
        showIcon
        message="关于当前可免费使用的模型"
        description="司命会读取 OpenCode 当前公开的免费模型池并逐个真实测试，不再只依赖单一模型。免费模型、额度和数据政策可能调整；小说内容会发送给所选云端模型处理，请勿提交私密或敏感内容。"
      />

      <Collapse ghost className="getting-started-alternatives" items={[{
        key: 'advanced',
        label: '高级选项',
        children: (
          <div className="getting-started-alternative-content">
            <Paragraph>通常不需要修改。模型不可用时，司命会自动尝试列表中的其他免费模型。</Paragraph>
            <Select
              value={selectedModel}
              onChange={setSelectedModel}
              options={status.free_models.map((model) => ({ value: model.id, label: model.recommended ? `${model.display_name}（推荐）` : model.display_name }))}
              placeholder="由司命自动选择"
              allowClear
              className="getting-started-model-select"
            />
            <Button icon={<SettingOutlined />} onClick={() => window.location.assign('/settings?section=ai')}>配置其他模型</Button>
          </div>
        ),
      }]} />
    </div>
  )
}

export default function GettingStartedPage() {
  const navigate = useNavigate()

  const deferSetup = () => {
    localStorage.setItem('siming_getting_started_deferred', 'true')
    navigate('/dashboard')
  }

  return (
    <PageWrapper maxWidth={1180} className="getting-started-page">
      <SystemNav current="getting-started" />
      <header className="siming-section-header getting-started-heading">
        <div>
          <span className="siming-section-kicker">第一次使用</span>
          <Title level={2}><RocketOutlined /> 免费开始写第一本小说</Title>
          <p className="siming-section-description">整个过程都在司命里完成，不要求你先学习任何技术设置。</p>
        </div>
        <Button icon={<ArrowRightOutlined />} onClick={deferSetup}>稍后设置</Button>
      </header>
      <GettingStartedPanel />
    </PageWrapper>
  )
}
