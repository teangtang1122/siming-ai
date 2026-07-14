import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}))

const fetchProjects = vi.fn()

vi.mock('../api/client', () => ({ apiClient: api }))
vi.mock('../stores', () => ({ useAppStore: () => ({ fetchProjects }) }))
vi.mock('../components/ContextGovernanceSettingsPanel', () => ({ default: () => null }))

import SettingsPage from '../pages/SettingsPage'

const launcherSettings = {
  launch_mode: 'desktop' as const,
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
    api.put.mockResolvedValue({ data: { data: { ...launcherSettings, launch_mode: 'browser' } } })
    api.post.mockResolvedValue({ data: { data: {
      current_version: '2.7.15',
      automatic_updates: false,
      update_available: true,
      update: { version: '2.8.0', source: 'https://example.test/release', download_url: 'https://example.test/Siming.exe', sha256_available: true },
      staged_update: null,
    } } })
  })

  it('does not check or download updates during initial load', async () => {
    render(<SettingsPage embedded />)

    expect(await screen.findByText('启动方式')).toBeInTheDocument()
    expect(screen.getByText('尚未检查更新。不会有后台下载或静默安装。')).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('saves browser mode for the next launch', async () => {
    render(<SettingsPage embedded />)

    const browserRadio = await screen.findByLabelText(/浏览器模式/)
    fireEvent.click(browserRadio)
    fireEvent.click(screen.getByRole('button', { name: '保存启动方式' }))

    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/config/launcher', { launch_mode: 'browser' }))
  })

  it('checks for an update only after the user clicks the button', async () => {
    render(<SettingsPage embedded />)

    await screen.findByText('安全更新')
    fireEvent.click(screen.getByRole('button', { name: '检查更新' }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/config/update/check'))
    expect(await screen.findByRole('button', { name: '下载并校验 2.8.0' })).toBeInTheDocument()
    expect(screen.getByText('发布页提供，下载后会复核')).toBeInTheDocument()
  })
})
