import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}))

vi.mock('../api/client', () => ({ apiClient: api }))

import { GettingStartedPanel } from '../pages/GettingStartedPage'

const baseStatus = {
  installed: false,
  command: null,
  version: null,
  managed_by_siming: false,
  model_source: 'none' as const,
  free_models: [],
  recommended_model: null,
  platform_supported: true,
  install_location: 'C:/Users/author/AppData/Local/Siming/managed-cli/opencode/bin/opencode.exe',
  configured: false,
  configured_model: null,
  is_global_default: false,
  has_any_model: false,
  needs_setup: true,
  global_model: null,
  official_links: {
    releases: 'https://github.com/anomalyco/opencode/releases/latest',
    install_docs: 'https://opencode.ai/docs/#install',
    model_docs: 'https://opencode.ai/docs/providers/#opencode-zen',
  },
}

const installedStatus = {
  ...baseStatus,
  installed: true,
  command: 'C:/managed/opencode.exe',
  version: '1.17.20',
  managed_by_siming: true,
  model_source: 'cli' as const,
  free_models: [{
    id: 'opencode/deepseek-v4-flash-free',
    display_name: 'opencode/deepseek-v4-flash-free',
    recommended: true,
  }],
  recommended_model: 'opencode/deepseek-v4-flash-free',
}

describe('GettingStartedPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('offers a one-click path without teaching command-line prerequisites', async () => {
    api.get.mockResolvedValue({ data: { data: baseStatus } })

    render(<MemoryRouter><GettingStartedPanel /></MemoryRouter>)

    expect(await screen.findByRole('button', { name: /一键安装 OpenCode/ })).toBeInTheDocument()
    expect(screen.getByText('不用命令行')).toBeInTheDocument()
    expect(screen.getByText('不用填写 API Key')).toBeInTheDocument()
    expect(screen.queryByText(/先安装 Node.js/)).not.toBeInTheDocument()
  })

  it('uses the lightweight status check when a model is already ready', async () => {
    api.get.mockResolvedValue({
      data: {
        data: {
          ...baseStatus,
          needs_setup: false,
          has_any_model: true,
          global_model: { provider: 'opencode_cli', model: 'opencode/free-model' },
        },
      },
    })

    render(<MemoryRouter><GettingStartedPanel /></MemoryRouter>)

    expect(await screen.findByText('司命已经可以使用 AI')).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledWith('/config/getting-started', { refresh: false, summary: true })
  })

  it('configures, tests, and promotes the selected free model', async () => {
    api.get.mockResolvedValue({ data: { data: installedStatus } })
    api.post.mockImplementation((url: string) => {
      if (url === '/config/getting-started/opencode/configure') {
        return Promise.resolve({ data: { data: {
          provider: 'opencode_cli',
          model: installedStatus.recommended_model,
          command: installedStatus.command,
          cli_args: '["run","--dangerously-skip-permissions","{prompt}"]',
          status: installedStatus,
        } } })
      }
      if (url === '/config/models/test') return Promise.resolve({ data: { data: { reply: '连接成功' } } })
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })
    api.put.mockResolvedValue({ data: { data: {} } })

    render(<MemoryRouter><GettingStartedPanel /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: /完成设置并测试/ }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/models/test', expect.objectContaining({
      provider: 'opencode_cli',
      model: installedStatus.recommended_model,
      timeout_seconds: 60,
    })))
    expect(api.put).toHaveBeenCalledWith('/config/global-model', {
      provider: 'opencode_cli',
      model: installedStatus.recommended_model,
    })
    expect(await screen.findByText('设置完成，可以开始体验')).toBeInTheDocument()
  })
})
