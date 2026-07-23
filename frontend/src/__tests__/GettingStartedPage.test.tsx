import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { createSimingQueryClient } from '../shared/query/client'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }))
vi.mock('../api/client', () => ({ apiClient: api }))

import { GettingStartedPanel } from '../pages/GettingStartedPage'

function renderPanel() {
  const client = createSimingQueryClient()
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GettingStartedPanel /></MemoryRouter>
    </QueryClientProvider>,
  )
}

const baseStatus = {
  free_models: [],
  recommended_model: null,
  platform_supported: true,
  configured: false,
  configured_model: null,
  is_global_default: false,
  needs_setup: true,
  global_model: null,
  activation_job: null,
  official_links: { model_docs: 'https://opencode.ai/docs/zen' },
}

describe('GettingStartedPanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('offers one plain-language activation action', async () => {
    api.get.mockResolvedValue({ data: { data: baseStatus } })
    api.post.mockResolvedValue({ data: { data: {
      id: 'job-1', status: 'pending', phase: 'checking', percent: 0,
      message: '免费体验任务已创建', free_models: [],
    } } })

    renderPanel()
    expect(await screen.findByText('从一句故事想法开始')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /准备 AI 并开始构思/ }))

    expect(screen.getByText('无需安装开发工具')).toBeInTheDocument()
    expect(screen.getByText('无需打开命令行')).toBeInTheDocument()
    expect(screen.getByText('每一步都能看到进度')).toBeInTheDocument()
    expect(screen.getByText(/逐个真实测试/)).toBeInTheDocument()
    expect(api.post).toHaveBeenCalledWith('/config/getting-started/opencode/activate', { preferred_model: null })
    expect(screen.queryByText(/先安装 Node.js/)).not.toBeInTheDocument()
  })

  it('shows the first-idea prompt as soon as a model is ready', async () => {
    api.get.mockResolvedValue({ data: { data: {
      ...baseStatus,
      needs_setup: false,
      configured: true,
      is_global_default: true,
      global_model: { provider: 'opencode_cli', model: 'opencode/free-model' },
    } } })

    renderPanel()

    expect(await screen.findByText('免费写作能力已经准备好')).toBeInTheDocument()
    expect(screen.getByLabelText('你想写什么故事？')).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledTimes(1)
  })

  it('creates a session and starts three concept generation from one idea', async () => {
    api.get.mockResolvedValue({ data: { data: {
      ...baseStatus, needs_setup: false, is_global_default: true,
      global_model: { provider: 'opencode_cli', model: 'opencode/free-model' },
    } } })
    api.post.mockImplementation((url: string) => {
      if (url === '/novel-creation/start') return Promise.resolve({ data: { data: { session_id: 'session-1' } } })
      if (url === '/novel-creation/sessions/session-1/runs') return Promise.resolve({ data: { data: { run: { id: 'run-1', status: 'running' } } } })
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })

    renderPanel()
    fireEvent.change(await screen.findByLabelText('你想写什么故事？'), { target: { value: '午夜客栈里的修仙少女' } })
    fireEvent.click(screen.getByRole('button', { name: /生成三套小说创意/ }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/novel-creation/start', expect.objectContaining({
      mode: 'internal_llm', user_brief: '午夜客栈里的修仙少女',
    })))
    expect(api.post).toHaveBeenCalledWith('/novel-creation/sessions/session-1/runs', expect.objectContaining({
      stage: 'concepts', model: 'opencode_cli:opencode/free-model',
    }))
  })

  it('starts managed official login without relying on a window-focus retry', async () => {
    const authJob = {
      id: 'job-auth', status: 'auth_required', phase: 'auth_required', percent: 90,
      message: '需要完成一次免费的官方登录', free_models: [],
    }
    api.get.mockResolvedValue({ data: { data: { ...baseStatus, activation_job: authJob } } })
    api.post.mockImplementation((url: string) => {
      if (url.endsWith('/authenticate')) return Promise.resolve({ data: { data: {
        ...authJob,
        status: 'running',
        phase: 'authenticating',
        auth_mode: 'managed_cli',
        auth_status: 'running',
        auth_prompt: '正在等待 OpenCode 官方登录',
      } } })
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })

    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: '开始官方登录' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/getting-started/opencode/jobs/job-auth/authenticate'))
    expect(await screen.findByText('正在等待 OpenCode 官方登录')).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalledWith('/config/getting-started/opencode/jobs/job-auth/retry')
  })

  it('explains a Windows certificate-chain failure without suggesting unsafe TLS bypasses', async () => {
    api.get.mockResolvedValue({ data: { data: {
      ...baseStatus,
      activation_job: {
        id: 'job-cert', status: 'failed', phase: 'failed', percent: 4,
        message: '免费写作能力暂时没有准备完成',
        failure_kind: 'certificate_verification',
        next_action: '请确认 Windows 日期和时间正确，并完成 Windows 更新后重试。司命会使用系统受信任证书，且不会关闭 HTTPS 校验。',
        error: '<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED]>',
        free_models: [],
      },
    } } })

    renderPanel()

    expect(await screen.findByText('Windows 证书验证没有完成')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /重新验证连接/ })).toBeInTheDocument()
    expect(screen.getByText(/不会关闭 HTTPS 校验/)).toBeInTheDocument()
  })

  it('distinguishes third-party free-pool limits from network failures and offers recovery', async () => {
    const quotaJob = {
      id: 'job-quota', status: 'failed', phase: 'failed', percent: 98,
      message: 'OpenCode 免费服务已限流',
      failure_kind: 'quota_or_rate_limit',
      next_action: '司命已实际测试 2 个免费模型，第三方均返回 403/429 或额度限制；这不是网络故障。',
      error: 'HTTP Error 403: rate limit exceeded',
      free_models: [
        { id: 'opencode/first-free', display_name: 'First Free', recommended: true, test_status: 'rate_limited' },
        { id: 'opencode/second-free', display_name: 'Second Free', recommended: false, test_status: 'rate_limited' },
      ],
    }
    api.get.mockResolvedValue({ data: { data: { ...baseStatus, activation_job: quotaJob } } })
    api.post.mockImplementation((url: string) => {
      if (url.endsWith('/authenticate')) return Promise.resolve({ data: { data: {
        ...quotaJob,
        status: 'running',
        phase: 'authenticating',
        auth_status: 'running',
      } } })
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })

    renderPanel()

    expect(await screen.findByText('OpenCode 免费服务已限流（不是网络故障）')).toBeInTheDocument()
    expect(screen.getByText(/已实际测试 2 个免费模型/)).toBeInTheDocument()
    expect(screen.queryByText(/请检查网络/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /重新检测免费模型/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '查看官方免费模型说明' })).toHaveAttribute('href', 'https://opencode.ai/docs/zen')

    fireEvent.click(screen.getByRole('button', { name: '登录后验证个人免费额度' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/getting-started/opencode/jobs/job-quota/authenticate'))
  })

  it('keeps a download limit separate from free-model quota', async () => {
    api.get.mockResolvedValue({ data: { data: {
      ...baseStatus,
      activation_job: {
        id: 'job-download', status: 'failed', phase: 'failed', percent: 5,
        message: '免费写作能力暂时没有准备完成',
        failure_kind: 'download_rate_limit',
        next_action: 'OpenCode 官方下载服务返回 403/429 限流；下载进度已保留。',
        error: 'HTTP Error 403: rate limit exceeded',
        free_models: [],
      },
    } } })

    renderPanel()

    expect(await screen.findByText('OpenCode 下载服务暂时限流')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /稍后继续下载/ })).toBeInTheDocument()
    expect(screen.queryByText('OpenCode 免费服务已限流（不是网络故障）')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '登录后验证个人免费额度' })).not.toBeInTheDocument()
  })
})
