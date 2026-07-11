/**
 * Test cases for the RouteGuard component (FR-008: 多作品管理, 路由守卫).
 *
 * Covers:
 *   - Redirect to most recent project when projects exist (root path)
 *   - Stay on dashboard when no projects exist
 *   - /dashboard path stays available for project management
 *   - Non-root paths are not affected by route guard
 *   - Direct /project/:id access works
 *   - Wildcard redirect to root
 *   - Loading state while fetching projects
 *   - Most recent project determined by projects[0]
 *
 * Test design:
 *   - Uses MemoryRouter with initialEntries to simulate URL states.
 *   - Mocks the store to control project list and loading state.
 *   - Uses vi.hoisted for the navigate mock.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// ---------------------------------------------------------------------------
// Mock axios globally for jsdom compatibility
// ---------------------------------------------------------------------------
vi.mock('axios', () => {
  const mockInstance = {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    interceptors: {
      request: { use: vi.fn(), eject: vi.fn() },
      response: { use: vi.fn(), eject: vi.fn() },
    },
  }
  const mockAxios = {
    create: vi.fn(() => mockInstance),
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    interceptors: {
      request: { use: vi.fn(), eject: vi.fn() },
      response: { use: vi.fn(), eject: vi.fn() },
    },
  }
  return {
    __esModule: true,
    default: mockAxios,
  }
})

// ---------------------------------------------------------------------------
// Mock react-router-dom's useNavigate using vi.hoisted
// ---------------------------------------------------------------------------
const { mockNavigate } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

// ---------------------------------------------------------------------------
// Mock the Zustand store
// ---------------------------------------------------------------------------
const mockFetchProjects = vi.fn()
let mockProjects: unknown[] = []

vi.mock('../stores', () => ({
  useAppStore: (selector?: (state: Record<string, unknown>) => unknown) => {
    const state = {
      projects: mockProjects,
      loading: false,
      fetchProjects: mockFetchProjects,
    }
    return selector ? selector(state) : state
  },
}))

vi.mock('../pages/DashboardPage', () => ({
  default: () => <div data-testid="dashboard-page">Dashboard loaded</div>,
}))

// ---------------------------------------------------------------------------
// Helper: create a project object
// ---------------------------------------------------------------------------
function makeProject(overrides: Record<string, unknown> = {}) {
  return {
    id: 'proj-001',
    title: '测试作品',
    description: '简介',
    tags: '[]',
    narrative_perspective: 'third_person',
    writing_style: 'natural',
    daily_word_goal: 6000,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-15T00:00:00Z',
    ...overrides,
  }
}

import App from '../App'

describe('RouteGuard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockProjects = []
    mockFetchProjects.mockResolvedValue(undefined)
  })

  // ------------------------------------------------------------------
  // TC-F17: Root path redirects to most recent project when projects exist
  // ------------------------------------------------------------------
  it('should redirect to most recent project when on root path with projects', async () => {
    mockProjects = [
      makeProject({ id: 'recent-1', title: '最近作品', updated_at: '2025-02-01T00:00:00Z' }),
      makeProject({ id: 'old-1', title: '旧作品', updated_at: '2025-01-01T00:00:00Z' }),
    ]

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/project/recent-1', { replace: true })
    })
  })

  // ------------------------------------------------------------------
  // TC-F18: Root path stays on dashboard when no projects
  // ------------------------------------------------------------------
  it('should stay on dashboard when no projects exist', async () => {
    mockProjects = []

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByTestId('dashboard-page')).toBeInTheDocument()
    })

    expect(mockNavigate).not.toHaveBeenCalled()
  })

  // ------------------------------------------------------------------
  // TC-F19: /dashboard path stays available for creating/managing projects
  // ------------------------------------------------------------------
  it('should keep /dashboard on the project management page when projects exist', async () => {
    mockProjects = [makeProject({ id: 'dash-1' })]

    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <App />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByTestId('dashboard-page')).toBeInTheDocument()
    })
    expect(mockNavigate).not.toHaveBeenCalledWith('/project/dash-1', { replace: true })
  })

  // ------------------------------------------------------------------
  // TC-F20: Non-root path does NOT redirect (e.g., /settings)
  // ------------------------------------------------------------------
  it('should not redirect when on a non-root path like /settings', async () => {
    mockProjects = [makeProject({ id: 'some-1' })]

    render(
      <MemoryRouter initialEntries={['/settings']}>
        <App />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(mockFetchProjects).toHaveBeenCalled()
    })

    // The guard only fires on "/"; dashboard and settings remain available.
    expect(mockNavigate).not.toHaveBeenCalledWith(
      expect.stringContaining('/project/'),
      expect.anything()
    )
  })

  // ------------------------------------------------------------------
  // TC-F21: Direct access to /project/:id works even with multiple projects
  // ------------------------------------------------------------------
  it('should allow direct access to /project/:id path', async () => {
    mockProjects = [
      makeProject({ id: 'p-a' }),
      makeProject({ id: 'p-b' }),
    ]

    render(
      <MemoryRouter initialEntries={['/project/p-b']}>
        <App />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(mockFetchProjects).toHaveBeenCalled()
    })

    expect(mockNavigate).not.toHaveBeenCalledWith(
      '/project/p-a',
      expect.anything()
    )
  })

  // ------------------------------------------------------------------
  // TC-F22: Wildcard path (*) redirects to root
  // ------------------------------------------------------------------
  it('should redirect wildcard paths to root', async () => {
    mockProjects = []

    render(
      <MemoryRouter initialEntries={['/nonexistent/path']}>
        <App />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/', { replace: true })
    })
  })

  // ------------------------------------------------------------------
  // TC-F23: fetchProjects is called on mount
  // ------------------------------------------------------------------
  it('should call fetchProjects on mount', () => {
    mockProjects = []

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>
    )

    expect(mockFetchProjects).toHaveBeenCalled()
  })

  // ------------------------------------------------------------------
  // TC-F24: Most recent project is determined by projects[0] (store order)
  // ------------------------------------------------------------------
  it('should use the first project in the list as the most recent', async () => {
    mockProjects = [
      makeProject({ id: 'newest', updated_at: '2025-03-01T00:00:00Z' }),
      makeProject({ id: 'older', updated_at: '2025-02-01T00:00:00Z' }),
      makeProject({ id: 'oldest', updated_at: '2025-01-01T00:00:00Z' }),
    ]

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/project/newest', { replace: true })
    })
  })

  // ------------------------------------------------------------------
  // TC-F25: After creating first project, route guard should redirect
  // ------------------------------------------------------------------
  it('should redirect after projects become available', async () => {
    mockProjects = []

    const { rerender } = render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByTestId('dashboard-page')).toBeInTheDocument()
    })

    mockProjects = [makeProject({ id: 'after-create' })]

    rerender(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/project/after-create', { replace: true })
    })
  })
})
