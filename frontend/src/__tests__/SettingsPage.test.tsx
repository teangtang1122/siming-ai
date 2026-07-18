import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}))

const invalidateQueries = vi.fn()

vi.mock('../api/client', () => ({ apiClient: api }))
vi.mock('@tanstack/react-query', () => ({ useQueryClient: () => ({ invalidateQueries }) }))
vi.mock('../components/ContextGovernanceSettingsPanel', () => ({ default: () => null }))

import SettingsPage from '../pages/SettingsPage'

const launcherSettings = {
  launch_mode: 'desktop' as const,
  update_channel: 'stable' as const,
  restart_required: true,
  browser_mode_description: 'Use the default browser.',
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

describe('SettingsPage startup and update controls', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockInitialLoads()
    api.put.mockImplementation((_url: string, payload: object) => Promise.resolve({
      data: { data: { ...launcherSettings, ...payload } },
    }))
    api.post.mockResolvedValue({ data: { data: {
      current_version: '2.8.0',
      update_channel: 'stable',
      automatic_updates: false,
      update_available: true,
      update: { version: '2.8.0', channel: 'stable', source: 'https://example.test/release', download_url: 'https://example.test/Siming.exe', sha256_available: true },
      staged_update: null,
    } } })
  })

  it('does not check or download updates during initial load', async () => {
    render(<SettingsPage embedded />)

    expect(await screen.findByText('可用模型')).toBeInTheDocument()
    expect(screen.getByText('检测到但尚未可用')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: '应用与数据' }))
    expect(await screen.findByText('启动方式')).toBeInTheDocument()
    expect(screen.getByText('尚未检查更新。不会有后台下载或静默安装。')).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('saves browser mode for the next launch', async () => {
    render(<SettingsPage embedded />)

    fireEvent.click(await screen.findByRole('tab', { name: '应用与数据' }))
    const browserRadio = await screen.findByLabelText(/浏览器模式/)
    fireEvent.click(browserRadio)
    fireEvent.click(screen.getByRole('button', { name: '保存启动方式' }))

    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/config/launcher', { launch_mode: 'browser' }))
  })

  it('checks for an update only after the user clicks the button', async () => {
    render(<SettingsPage embedded />)

    fireEvent.click(await screen.findByRole('tab', { name: '应用与数据' }))
    await screen.findByText('安全更新')
    fireEvent.click(screen.getByRole('button', { name: '检查更新' }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/update/check'))
    expect(await screen.findByRole('button', { name: '下载并校验 2.8.0' })).toBeInTheDocument()
    expect(screen.getByText('发布页提供，下载后会复核')).toBeInTheDocument()
  })

  it('saves the preview channel explicitly', async () => {
    render(<SettingsPage embedded />)

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

    render(<SettingsPage embedded />)
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
    expect(await screen.findByText('模型真实回复成功（Responses API）')).toBeInTheDocument()
  })
})
