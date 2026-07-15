import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }))
vi.mock('../api/client', () => ({ apiClient: api }))

import { GettingStartedPanel } from '../pages/GettingStartedPage'

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
}

describe('GettingStartedPanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('offers one plain-language activation action', async () => {
    api.get.mockResolvedValue({ data: { data: baseStatus } })
    api.post.mockResolvedValue({ data: { data: {
      id: 'job-1', status: 'pending', phase: 'checking', percent: 0,
      message: '免费体验任务已创建', free_models: [],
    } } })

    render(<MemoryRouter><GettingStartedPanel /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: /免费开始写小说/ }))

    expect(screen.getByText('不用命令行')).toBeInTheDocument()
    expect(screen.getByText('不用填写 API Key')).toBeInTheDocument()
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

    render(<MemoryRouter><GettingStartedPanel /></MemoryRouter>)

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

    render(<MemoryRouter><GettingStartedPanel /></MemoryRouter>)
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

    render(<MemoryRouter><GettingStartedPanel /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: '开始官方登录' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/getting-started/opencode/jobs/job-auth/authenticate'))
    expect(await screen.findByText('正在等待 OpenCode 官方登录')).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalledWith('/config/getting-started/opencode/jobs/job-auth/retry')
  })
})
