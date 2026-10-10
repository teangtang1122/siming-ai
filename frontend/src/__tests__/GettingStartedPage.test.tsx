import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { GatewayRuntimeContext } from '../components/GatewayRuntimeContext'

const { api, navigate } = vi.hoisted(() => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn() }, navigate: vi.fn(),
}))
vi.mock('../api/client', () => ({ apiClient: api }))
vi.mock('react-router-dom', async (original) => ({
  ...await original<typeof import('react-router-dom')>(), useNavigate: () => navigate,
}))

import GettingStartedPage, { GettingStartedPanel } from '../pages/GettingStartedPage'

function renderPanel({ page = false, headless = false } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <GatewayRuntimeContext.Provider value={{ headless }}>
        <MemoryRouter>{page ? <GettingStartedPage /> : <GettingStartedPanel />}</MemoryRouter>
      </GatewayRuntimeContext.Provider>
    </QueryClientProvider>,
  )
}

const emptyStatus = {
  has_any_model: false, has_usable_models: false, needs_setup: true,
  global_model: null, available_model: null,
}
const response = (data: unknown) => ({ data: { data } })

describe('GettingStartedPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    api.get.mockResolvedValue(response(emptyStatus))
  })

  it('offers API setup and official key portals without starting model operations', async () => {
    renderPanel()
    expect(await screen.findByText('通过 API 连接模型')).toBeInTheDocument()
    for (const [name, href] of [
      ['DeepSeek', 'https://platform.deepseek.com/api_keys'],
      ['通义千问', 'https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key'],
      ['OpenAI', 'https://platform.openai.com/api-keys'],
      ['Anthropic Claude', 'https://platform.claude.com/settings/keys'],
      ['Google Gemini', 'https://aistudio.google.com/apikey'],
    ]) {
      const link = screen.getByRole('link', { name: `${name} 官网获取 API Key` })
      expect(link).toHaveAttribute('href', href)
      expect(link).toHaveAttribute('target', '_blank')
      expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    }
    expect(screen.queryByRole('button', { name: '打开本地模型中心' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '配置 CLI' })).not.toBeInTheDocument()
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledWith('/config/getting-started')
    expect(api.post).not.toHaveBeenCalled()
    expect(api.put).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '配置 API' }))
    expect(navigate).toHaveBeenLastCalledWith('/settings?section=ai&setup=api')
  })

  it('shows saved unverified configurations as needing verification', async () => {
    api.get.mockResolvedValue(response({ ...emptyStatus, has_any_model: true }))
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: '查看并验证' }))
    expect(navigate).toHaveBeenCalledWith('/settings?section=ai')
    expect(screen.queryByLabelText('你想写什么故事？')).not.toBeInTheDocument()
  })

  it('refreshes persisted readiness and moves directly to creation after setup', async () => {
    api.get.mockResolvedValueOnce(response(emptyStatus)).mockResolvedValue(response({
      ...emptyStatus, has_any_model: true, has_usable_models: true, needs_setup: false,
      available_model: { provider: 'deepseek', model: 'author-model' },
    }))
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: '刷新配置状态' }))
    expect(await screen.findByText('模型已就绪，开始构思吧')).toBeInTheDocument()
    expect(screen.getByText('本次使用：DeepSeek · author-model')).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledTimes(2)
    expect(api.post).not.toHaveBeenCalled()
  })

  it.each([
    ['deepseek', 'author-model'], ['opencode_cli', 'vendor/authorized-model'],
    ['local_llama_cpp', 'local-writing-model'],
  ])('starts one concept run with the displayed verified %s model', async (provider, model) => {
    api.get.mockResolvedValue(response({
      ...emptyStatus, has_any_model: true, has_usable_models: true, needs_setup: false,
      global_model: { provider, model },
      available_model: { provider: 'other', model: 'not-selected' },
    }))
    api.post.mockImplementation((url: string) => {
      if (url === '/novel-creation/start') return Promise.resolve(response({ session_id: 'session-1' }))
      if (url === '/novel-creation/sessions/session-1/runs') return Promise.resolve(response({ run: { id: 'run-1' } }))
      return Promise.reject(new Error(`Unexpected POST ${url}`))
    })
    renderPanel()
    fireEvent.change(await screen.findByLabelText('你想写什么故事？'), { target: { value: '午夜客栈里的修仙少女' } })
    fireEvent.click(screen.getByRole('button', { name: '生成小说创意' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/novel-creation/sessions/session-1/runs',
      expect.objectContaining({ stage: 'concepts', model: `${provider}:${model}` })))
    expect(api.post).toHaveBeenCalledWith('/novel-creation/start', expect.objectContaining({
      mode: 'internal_llm', user_brief: '午夜客栈里的修仙少女',
    }))
    expect(api.post).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(navigate).toHaveBeenCalled())
  })

  it('supports retrying a failed status read without showing misleading setup success', async () => {
    api.get.mockRejectedValueOnce(new Error('服务暂时不可达')).mockResolvedValue(response(emptyStatus))
    renderPanel()
    expect(await screen.findByText('服务暂时不可达')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByText('通过 API 连接模型')).toBeInTheDocument()
  })

  it('offers only API setup on a headless Gateway', async () => {
    renderPanel({ headless: true })
    expect(await screen.findByRole('button', { name: '配置 API' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '配置 CLI' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '打开本地模型中心' })).not.toBeInTheDocument()
  })

  it('allows manual creation before configuring any model', async () => {
    renderPanel({ page: true })
    fireEvent.click(await screen.findByRole('button', { name: '稍后设置' }))
    expect(localStorage.getItem('siming_getting_started_deferred')).toBe('true')
    expect(navigate).toHaveBeenCalledWith('/dashboard')
    expect(api.post).not.toHaveBeenCalled()
  })
})
