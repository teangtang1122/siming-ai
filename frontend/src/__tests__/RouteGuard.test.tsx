import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const { mockNavigate, mockFetchProjects } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockFetchProjects: vi.fn(),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

vi.mock('../stores', () => ({
  useAppStore: (selector?: (state: Record<string, unknown>) => unknown) => {
    const state = { projects: [], loading: false, fetchProjects: mockFetchProjects, error: null, setError: vi.fn() }
    return selector ? selector(state) : state
  },
}))

vi.mock('../pages/DashboardPage', () => ({ default: () => <div data-testid="dashboard-page">作品库</div> }))
vi.mock('../pages/ProjectWorkspace', () => ({ default: () => <div data-testid="project-page">项目工作区</div> }))
vi.mock('../pages/SettingsPage', () => ({ default: () => <div data-testid="settings-page">设置</div> }))

import App from '../App'

describe('App route behavior', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetchProjects.mockResolvedValue(undefined)
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

  it('preloads project metadata on system routes outside the library', async () => {
    render(<MemoryRouter initialEntries={['/settings']}><App /></MemoryRouter>)
    expect(await screen.findByTestId('settings-page')).toBeInTheDocument()
    await waitFor(() => expect(mockFetchProjects).toHaveBeenCalled())
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
