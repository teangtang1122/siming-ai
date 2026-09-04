import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('../api/client', () => ({ apiClient: api }))
vi.mock('../components/ContextGovernanceSettingsPanel', () => ({ default: () => null }))

import SettingsPage from '../pages/SettingsPage'

const launcherSettings = {
  launch_mode: 'desktop' as const,
  update_channel: 'stable' as const,
  restart_required: true,
  browser_mode_description: 'Use the default browser.',
}

function renderSettings(extra?: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <SettingsPage embedded />
      {extra}
    </QueryClientProvider>,
  )
}

function mockInitialLoads() {
  api.get.mockImplementation((url: string) => {
    if (url === '/config/models') return Promise.resolve({ data: { data: { items: [] } } })
    if (url === '/config/global-model') return Promise.resolve({ data: { data: { provider: null, model: null } } })
    if (url === '/config/content-root') {
      return Promise.resolve({ data: { data: {
        current_path: 'D:/Siming/projects', default_path: 'D:/Siming/projects', is_default: true,
        exists: true, is_empty: true,
      } } })
    }
    if (url === '/config/launcher') return Promise.resolve({ data: { data: launcherSettings } })
    return Promise.resolve({ data: { data: {} } })
  })
}

function mockCustomModelConfig() {
  api.get.mockImplementation((url: string) => {
    if (url === '/config/models') return Promise.resolve({ data: { data: { items: [{
      id: 'vendor-config',
      provider: 'vendor',
      default_model: 'legacy-model',
      base_url_override: 'https://api.vendor.example',
      api_protocol: 'auto',
      provider_type: 'api',
      readiness_status: 'unverified',
      readiness_message: '待验证',
      is_usable: false,
      is_global_default: false,
    }] } } })
    if (url === '/config/global-model') return Promise.resolve({ data: { data: { provider: null, model: null } } })
    if (url === '/config/content-root') return Promise.resolve({ data: { data: {
      current_path: 'D:/Siming/projects', default_path: 'D:/Siming/projects', is_default: true,
      exists: true, is_empty: true,
    } } })
    if (url === '/config/launcher') return Promise.resolve({ data: { data: launcherSettings } })
    return Promise.resolve({ data: { data: {} } })
  })
}

describe('SettingsPage startup and update controls', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.history.replaceState({}, '', '/settings')
    mockInitialLoads()
    api.put.mockImplementation((_url: string, payload: object) => Promise.resolve({
      data: { data: { ...launcherSettings, ...payload } },
    }))
    api.post.mockResolvedValue({ data: { data: {
      current_version: '2.8.0',
      update_channel: 'stable',
      automatic_updates: false,
      update_available: true,
      update: {
        version: '2.8.0', channel: 'stable', source: 'https://example.test/release',
        download_url: 'https://example.test/Siming.exe', sha256_available: true,
        download_sources: [
          { key: 'github', label: 'GitHub', download_url: 'https://github.test/Siming-Setup.exe', releases_url: 'https://github.test/releases' },
          { key: 'gitee', label: 'Gitee 国内镜像', download_url: 'https://gitee.test/Siming-Setup.exe', releases_url: 'https://gitee.test/releases' },
        ],
      },
      staged_update: null,
      manual_download_pages: [
        { key: 'gitee', label: 'Gitee 镜像下载', url: 'https://gitee.test/releases', description: '大陆网络备用' },
        { key: 'github', label: 'GitHub 全部版本', url: 'https://github.test/releases', description: '完整历史版本' },
      ],
    } } })
  })

  it('opens the advanced context panel from the model-capacity remediation link', async () => {
    window.history.replaceState({}, '', '/settings?section=context-governance')
    renderSettings()

    const advanced = await screen.findByRole('button', { name: /高级设置：上下文与技术参数/ })
    expect(advanced).toHaveAttribute('aria-expanded', 'true')
    expect(document.getElementById('context-governance-settings')).toBeInTheDocument()
  })

  it('does not check or download updates during initial load', async () => {
    renderSettings()

    expect(await screen.findByText('可用模型')).toBeInTheDocument()
    expect(screen.getByText('检测到但尚未可用')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: '应用与数据' }))
    expect(await screen.findByText('启动方式')).toBeInTheDocument()
    expect(screen.getByText('尚未检查更新。不会有后台下载或静默安装。')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Gitee 镜像下载/ })).toHaveAttribute(
      'href',
      'https://gitee.com/teangtang13/siming-ai/releases',
    )
    expect(screen.getByRole('link', { name: /GitHub 全部版本/ })).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('assigns a secondary discovered model to a task without changing the global default', async () => {
    api.get.mockImplementation((url: string) => {
      if (url === '/config/models') return Promise.resolve({ data: { data: {
        items: [{
          id: 'openai-ready', provider: 'openai', default_model: 'gpt-4o',
          available_models: [
            { id: 'gpt-4o', display_name: 'GPT 4o' },
            { id: 'gpt-4.1-mini', display_name: 'GPT Mini' },
          ],
          provider_type: 'api', readiness_status: 'ready', readiness_message: '可用',
          is_usable: true, is_global_default: true,
        }],
        task_models: {},
      } } })
      if (url === '/config/content-root') return Promise.resolve({ data: { data: {
        current_path: 'D:/Siming/projects', default_path: 'D:/Siming/projects', is_default: true,
        exists: true, is_empty: true,
      } } })
      if (url === '/config/launcher') return Promise.resolve({ data: { data: launcherSettings } })
      return Promise.resolve({ data: { data: {} } })
    })
    api.put.mockImplementation((url: string, payload: Record<string, unknown>) => {
      if (url === '/config/task-models/writing') return Promise.resolve({ data: { data: {
        task_type: 'writing',
        ...payload,
        is_usable: true,
      } } })
      return Promise.resolve({ data: { data: { ...launcherSettings, ...payload } } })
    })

    renderSettings()
    expect(await screen.findByText('按任务选择模型')).toBeInTheDocument()
    const writingModel = screen.getByRole('combobox', { name: '章节写作默认模型' })
    await waitFor(() => expect(writingModel).not.toBeDisabled())
    fireEvent.mouseDown(writingModel)
    fireEvent.click(await screen.findByText('OpenAI · GPT Mini'))

    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/config/task-models/writing', {
      provider: 'openai',
      model: 'gpt-4.1-mini',
      context_length: null,
    }))
    expect(api.put).not.toHaveBeenCalledWith('/config/global-model', expect.anything())
  })

  it('scans and configures each CLI only after separate user actions', async () => {
    api.post.mockImplementation((url: string) => {
      if (url === '/config/cli-integrations/scan') {
        return Promise.resolve({ data: { data: {
          status: 'scanned', detected_count: 1, supported_count: 10,
          clients: [{
            provider: 'codex_cli', label: 'Codex CLI', detected: true,
            command: 'C:/tools/codex.cmd', config_path: 'C:/Users/test/.codex/config.toml',
            configured: false, can_restore: false,
          }],
        } } })
      }
      if (url === '/config/cli-integrations/codex_cli/configure') {
        return Promise.resolve({ data: { data: {
          provider: 'codex_cli', label: 'Codex CLI', status: 'configured',
          configured: true, can_restore: true,
        } } })
      }
      return Promise.resolve({ data: { data: {} } })
    })

    renderSettings()
    expect(await screen.findByText('尚未扫描本机 CLI')).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalledWith('/config/cli-integrations/scan')

    fireEvent.click(screen.getByRole('button', { name: '扫描本机 CLI' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/cli-integrations/scan'))
    expect(await screen.findByText('Codex CLI')).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalledWith('/config/cli-integrations/codex_cli/configure')

    fireEvent.click(screen.getByRole('button', { name: '自动配置 Codex CLI' }))
    expect((await screen.findAllByText('允许司命自动配置 Codex CLI？')).length).toBeGreaterThan(0)
    expect(screen.getByText(/不会改变该 CLI 现有的沙箱、审批或工具权限设置/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '我知情，自动配置' }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/cli-integrations/codex_cli/configure'))
  })

  it('saves browser mode for the next launch', async () => {
    renderSettings()

    fireEvent.click(await screen.findByRole('tab', { name: '应用与数据' }))
    const browserRadio = await screen.findByLabelText(/浏览器模式/)
    fireEvent.click(browserRadio)
    fireEvent.click(screen.getByRole('button', { name: '保存启动方式' }))

    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/config/launcher', { launch_mode: 'browser' }))
  })

  it('checks for an update only after the user clicks the button', async () => {
    renderSettings()

    fireEvent.click(await screen.findByRole('tab', { name: '应用与数据' }))
    await screen.findByText('安全更新')
    fireEvent.click(screen.getByRole('button', { name: '检查更新' }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/update/check'))
    expect(await screen.findByRole('button', { name: '从Gitee 国内镜像下载并校验 2.8.0' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '从GitHub下载并校验 2.8.0' })).toBeInTheDocument()
    expect(screen.getByText(/两条线路地位相同/)).toBeInTheDocument()
    expect(screen.getByText('发布页提供，下载后会复核')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '从Gitee 国内镜像下载并校验 2.8.0' }))
    expect((await screen.findAllByText('通过 Gitee 国内镜像 下载司命 2.8.0？')).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: '下载并校验' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/update/download', { source: 'gitee' }))
  })

  it('saves the preview channel explicitly', async () => {
    renderSettings()

    fireEvent.click(await screen.findByRole('tab', { name: '应用与数据' }))
    fireEvent.click(await screen.findByLabelText(/预览通道/))
    fireEvent.click(screen.getByRole('button', { name: '保存更新通道' }))

    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/config/launcher', {
      update_channel: 'preview',
    }))
  })

  it('tests a custom Responses endpoint with the configured model instead of listing models', async () => {
    api.get.mockImplementation((url: string) => {
      if (url === '/config/models') return Promise.resolve({ data: { data: { items: [{
        id: 'yls-config',
        provider: 'yls',
        default_model: 'gpt-5.6-sol',
        base_url_override: 'https://code.example/codex',
        api_protocol: 'responses',
        provider_type: 'api',
        readiness_status: 'unverified',
        readiness_message: '待验证',
        is_usable: false,
        is_global_default: false,
      }] } } })
      if (url === '/config/global-model') return Promise.resolve({ data: { data: { provider: null, model: null } } })
      if (url === '/config/content-root') return Promise.resolve({ data: { data: {
        current_path: 'D:/Siming/projects', default_path: 'D:/Siming/projects', is_default: true,
        exists: true, is_empty: true,
      } } })
      if (url === '/config/launcher') return Promise.resolve({ data: { data: launcherSettings } })
      return Promise.resolve({ data: { data: {} } })
    })
    api.post.mockImplementation((url: string) => {
      if (url === '/config/models/test') {
        return Promise.resolve({ data: { data: { api_protocol: 'responses', base_url: 'https://code.example/codex' } } })
      }
      return Promise.resolve({ data: { data: {} } })
    })

    renderSettings()
    fireEvent.click(await screen.findByText('检测到但尚未可用'))
    fireEvent.click(await screen.findByRole('button', { name: /编辑/ }))
    fireEvent.change(await screen.findByLabelText('API Key'), { target: { value: 'secret-key' } })
    fireEvent.click(screen.getByRole('button', { name: /用当前模型真实测试/ }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/models/test', expect.objectContaining({
      provider: 'yls',
      api_key: 'secret-key',
      base_url_override: 'https://code.example/codex',
      api_protocol: 'responses',
      model: 'gpt-5.6-sol',
    })))
    expect(await screen.findByText(
      '模型基础对话探测成功（Responses API）；长任务仍可能受到临时限流或服务容量影响',
    )).toBeInTheDocument()
  })

  it('lets users revalidate a ready model instead of trusting stale status forever', async () => {
    api.get.mockImplementation((url: string) => {
      if (url === '/config/models') return Promise.resolve({ data: { data: { items: [{
        id: 'deepseek-config', provider: 'deepseek', default_model: 'deepseek-v4-flash',
        provider_type: 'api', readiness_status: 'ready', readiness_message: '真实对话测试成功',
        is_usable: true, is_global_default: true, last_tested_at: '2026-08-01T12:00:00Z',
      }] } } })
      if (url === '/config/global-model') return Promise.resolve({ data: { data: {
        provider: 'deepseek', model: 'deepseek-v4-flash',
      } } })
      if (url === '/config/content-root') return Promise.resolve({ data: { data: {
        current_path: 'D:/Siming/projects', default_path: 'D:/Siming/projects', is_default: true,
        exists: true, is_empty: true,
      } } })
      if (url === '/config/launcher') return Promise.resolve({ data: { data: launcherSettings } })
      return Promise.resolve({ data: { data: {} } })
    })
    api.post.mockResolvedValue({ data: {
      message: '模型已经通过真实对话测试', data: { became_global_default: false },
    } })

    renderSettings()
    expect(await screen.findByText(/上次验证：/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /重新验证/ }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/models/deepseek/verify'))
  })

  it('automatically discovers models for a custom provider after credentials are complete', async () => {
    mockCustomModelConfig()
    api.post.mockImplementation((url: string) => {
      if (url === '/config/models/list') {
        return Promise.resolve({ data: { data: {
          models: [{ id: 'vendor-model', display_name: 'Vendor Model' }],
          manual_entry_required: false,
          warning: null,
        } } })
      }
      return Promise.resolve({ data: { data: {} } })
    })

    renderSettings()
    fireEvent.click(await screen.findByText('检测到但尚未可用'))
    fireEvent.click(await screen.findByRole('button', { name: /编辑/ }))
    const apiKey = await screen.findByLabelText('API Key')
    fireEvent.change(apiKey, { target: { value: 'secret-key' } })
    fireEvent.blur(apiKey)

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/models/list', {
      provider: 'vendor',
      api_key: 'secret-key',
      base_url_override: 'https://api.vendor.example',
    }))
    expect(await screen.findByText('已自动拉取 1 个模型，请选择默认模型。')).toBeInTheDocument()
    fireEvent.mouseDown(screen.getByLabelText('默认模型'))
    expect(await screen.findByText('Vendor Model')).toBeInTheDocument()
  })

  it('reuses a saved API key when opening and saving another model', async () => {
    mockCustomModelConfig()
    api.get.mockImplementation((url: string) => {
      if (url === '/config/models') return Promise.resolve({ data: { data: { items: [{
        id: 'vendor-config', provider: 'vendor', default_model: 'legacy-model',
        base_url_override: 'https://api.vendor.example', api_protocol: 'auto', provider_type: 'api',
        readiness_status: 'unverified', readiness_message: '待验证', is_usable: false,
        is_global_default: false, api_key_configured: true,
        context_window_tokens: 96_000, context_safety_margin_tokens: 512,
        context_profile_source: 'configured', context_profile_known: true,
      }] } } })
      if (url === '/config/global-model') return Promise.resolve({ data: { data: { provider: null, model: null } } })
      if (url === '/config/content-root') return Promise.resolve({ data: { data: { current_path: 'D:/Siming/projects', default_path: 'D:/Siming/projects', is_default: true, exists: true, is_empty: true } } })
      if (url === '/config/launcher') return Promise.resolve({ data: { data: launcherSettings } })
      return Promise.resolve({ data: { data: {} } })
    })
    api.post.mockImplementation((url: string) => {
      if (url === '/config/models/list') return Promise.resolve({ data: { data: { models: [{ id: 'vendor-new', display_name: 'Vendor New' }] } } })
      return Promise.resolve({ data: { data: {} } })
    })

    renderSettings()
    fireEvent.click(await screen.findByText('检测到但尚未可用'))
    fireEvent.click(await screen.findByRole('button', { name: /编辑/ }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/models/list', {
      provider: 'vendor', api_key: undefined, base_url_override: 'https://api.vendor.example',
    }))
    expect(screen.getByPlaceholderText('已保存，留空继续使用')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^OK$/ }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/models', expect.objectContaining({ provider: 'vendor' })))
    const saveCall = api.post.mock.calls.find(([url]) => url === '/config/models')
    expect(saveCall?.[1]?.api_key).toBeFalsy()
    expect(saveCall?.[1]?.context_window_tokens).toBe(96_000)
  })

  it('uses the 256K fallback when an unknown custom model has no capacity profile', async () => {
    mockCustomModelConfig()
    api.post.mockImplementation((url: string) => {
      if (url === '/config/models/list') {
        return Promise.resolve({ data: { data: { models: [
          { id: 'legacy-model', display_name: 'Legacy Model' },
        ] } } })
      }
      return Promise.resolve({ data: { data: {} } })
    })

    renderSettings()
    fireEvent.click(await screen.findByText('检测到但尚未可用'))
    fireEvent.click(await screen.findByRole('button', { name: /编辑/ }))

    const capacity = await screen.findByLabelText('模型上下文窗口 tokens')
    expect(capacity).not.toBeDisabled()
    expect(screen.getByText(/可留空；司命会按 256,000 tokens 临时兜底/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^OK$/ }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/models', expect.objectContaining({
      provider: 'vendor',
      default_model: 'legacy-model',
      context_window_tokens: null,
    })))
  })

  it('asks OpenCode CLI itself for available models', async () => {
    api.get.mockImplementation((url: string) => {
      if (url === '/config/models') return Promise.resolve({ data: { data: { items: [{
        id: 'opencode', provider: 'opencode_cli', default_model: 'opencode/big-pickle', provider_type: 'local_cli',
        cli_command: 'opencode', cli_args: '', readiness_status: 'unverified', readiness_message: '待验证',
        is_usable: false, is_global_default: false,
      }] } } })
      if (url === '/config/global-model') return Promise.resolve({ data: { data: { provider: null, model: null } } })
      if (url === '/config/content-root') return Promise.resolve({ data: { data: { current_path: 'D:/Siming/projects', default_path: 'D:/Siming/projects', is_default: true, exists: true, is_empty: true } } })
      if (url === '/config/launcher') return Promise.resolve({ data: { data: launcherSettings } })
      return Promise.resolve({ data: { data: {} } })
    })
    api.post.mockImplementation((url: string) => {
      if (url === '/config/models/list') return Promise.resolve({ data: { data: { models: [
        { id: 'opencode/big-pickle', display_name: 'opencode/big-pickle' },
        { id: 'opencode/mimo-v2.5-free', display_name: 'opencode/mimo-v2.5-free' },
        { id: 'opencode/hy3-free', display_name: 'opencode/hy3-free' },
        { id: 'opencode/nemotron-3-ultra-free', display_name: 'opencode/nemotron-3-ultra-free' },
        { id: 'opencode/nemotron-3.5-lightning-free', display_name: 'opencode/nemotron-3.5-lightning-free' },
        { id: 'opencode/x-preview-f-free', display_name: 'opencode/x-preview-f-free' },
        { id: 'opencode/muse-spark-1.2-contributor-free', display_name: 'opencode/muse-spark-1.2-contributor-free' },
      ] } } })
      return Promise.resolve({ data: { data: {} } })
    })

    renderSettings()
    fireEvent.click(await screen.findByText('检测到但尚未可用'))
    fireEvent.click(await screen.findByRole('button', { name: /编辑/ }))

    expect(await screen.findByText(/已由司命运行 OpenCode CLI/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /刷新 OpenCode 模型/ })).toBeInTheDocument()
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/models/list', expect.objectContaining({
      provider: 'opencode_cli', cli_command: 'opencode',
    })))
    fireEvent.mouseDown(screen.getByLabelText('默认模型'))
    expect(await screen.findByText('opencode/mimo-v2.5-free')).toBeInTheDocument()
  })

  it('allows manual custom model entry only after automatic discovery fails', async () => {
    mockCustomModelConfig()
    api.post.mockImplementation((url: string) => {
      if (url === '/config/models/list') return Promise.reject(new Error('HTTP 404'))
      return Promise.resolve({ data: { data: {} } })
    })

    renderSettings()
    fireEvent.click(await screen.findByText('检测到但尚未可用'))
    fireEvent.click(await screen.findByRole('button', { name: /编辑/ }))
    expect(screen.queryByPlaceholderText('例如 openai/gpt-4o-mini 或 vendor-model-name')).not.toBeInTheDocument()
    const apiKey = await screen.findByLabelText('API Key')
    fireEvent.change(apiKey, { target: { value: 'secret-key' } })
    fireEvent.blur(apiKey)

    expect(await screen.findByText(/自动拉取模型失败：HTTP 404/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText('例如 openai/gpt-4o-mini 或 vendor-model-name')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /重新拉取/ })).toBeInTheDocument()
  })
})
