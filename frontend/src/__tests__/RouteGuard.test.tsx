import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const { mockNavigate, mockApiGet } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockApiGet: vi.fn(),
}))

vi.mock('../api/client', () => ({ apiClient: { get: mockApiGet } }))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

let needsSetup = false
vi.mock('../features/onboarding', () => ({
  useGettingStartedSummary: () => ({ data: { needs_setup: needsSetup } }),
}))
vi.mock('../stores', () => ({ useAppStore: (selector: (state: Record<string, unknown>) => unknown) => selector({ error: null, setError: vi.fn() }) }))
vi.mock('../features/operations/components/GlobalOperationCenter', () => ({ default: () => null }))

vi.mock('../pages/DashboardPage', () => ({ default: () => <div data-testid="dashboard-page">作品库</div> }))
vi.mock('../pages/ProjectWorkspace', () => ({ default: () => <div data-testid="project-page">项目工作区</div> }))
vi.mock('../pages/SettingsPage', () => ({ default: () => <div data-testid="settings-page">设置</div> }))

import App from '../App'

describe('App route behavior', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    needsSetup = false
    mockApiGet.mockResolvedValue({ data: { data: { needs_setup: false } } })
  })

  it('keeps the root path on the project library instead of silently opening a project', async () => {
    render(<MemoryRouter initialEntries={['/']}><App /></MemoryRouter>)
    expect(await screen.findByTestId('dashboard-page')).toBeInTheDocument()
    expect(mockNavigate).not.toHaveBeenCalledWith(expect.stringContaining('/project/'), expect.anything())
  })

  it('keeps the explicit dashboard route on the project library', async () => {
    render(<MemoryRouter initialEntries={['/dashboard']}><App /></MemoryRouter>)
    expect(await screen.findByTestId('dashboard-page')).toBeInTheDocument()
  })

  it('sends an unconfigured first-time author to free setup', async () => {
    needsSetup = true
    render(<MemoryRouter initialEntries={['/']}><App /></MemoryRouter>)
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/getting-started', { replace: true }))
  })

  it('opens system routes without a global project preload', async () => {
    render(<MemoryRouter initialEntries={['/settings']}><App /></MemoryRouter>)
    expect(await screen.findByTestId('settings-page')).toBeInTheDocument()
  })

  it('allows direct project access', async () => {
    render(<MemoryRouter initialEntries={['/project/p-b']}><App /></MemoryRouter>)
    expect(await screen.findByTestId('project-page')).toBeInTheDocument()
    expect(mockNavigate).not.toHaveBeenCalledWith('/', expect.anything())
  })

  it('redirects unknown paths to the stable root library', async () => {
    render(<MemoryRouter initialEntries={['/not-a-route']}><App /></MemoryRouter>)
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/', { replace: true }))
  })
})
