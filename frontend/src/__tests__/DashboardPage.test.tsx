/**
 * Test cases for the DashboardPage component (FR-008: 多作品管理).
 *
 * Covers:
 *   - Empty state rendering (no projects)
 *   - Project list display (card grid)
 *   - Search functionality
 *   - Create / Edit / Delete modals
 *   - Navigation on project card click
 *
 * Test design:
 *   - Does NOT rely on a running backend; uses mock store.
 *   - antd components rendered with proper wrappers.
 *   - Each test isolates its own store state.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { Modal } from 'antd'

// ---------------------------------------------------------------------------
// Mock axios globally for jsdom compatibility
// ---------------------------------------------------------------------------
const { mockApiGet, mockApiPostForm } = vi.hoisted(() => ({
  mockApiGet: vi.fn(),
  mockApiPostForm: vi.fn(),
}))

vi.mock('axios', () => {
  const mockInstance = {
    get: mockApiGet,
    post: vi.fn(),
    postForm: mockApiPostForm,
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

import DashboardPage from '../pages/DashboardPage'

// ---------------------------------------------------------------------------
// Mock the Zustand store
// ---------------------------------------------------------------------------
const mockFetchProjects = vi.fn()
const mockCreateProject = vi.fn()
const mockUpdateProject = vi.fn()
const mockDeleteProject = vi.fn()

let mockProjects: unknown[] = []
let mockLoading = false
let mockNeedsSetup = false

vi.mock('../features/projects', () => ({
  useProjects: (keyword?: string) => {
    mockFetchProjects(keyword)
    return {
      data: { items: mockProjects, total: mockProjects.length },
      isLoading: mockLoading,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    }
  },
  useCreateProject: () => ({ mutateAsync: mockCreateProject }),
  useUpdateProject: () => ({ mutateAsync: mockUpdateProject }),
  useDeleteProject: () => ({ mutateAsync: mockDeleteProject }),
}))

vi.mock('../features/onboarding', () => ({
  useGettingStartedSummary: () => ({ data: { needs_setup: mockNeedsSetup } }),
}))

// ---------------------------------------------------------------------------
// Helper: create a project item
// ---------------------------------------------------------------------------
function makeProject(overrides: Record<string, unknown> = {}) {
  return {
    id: 'proj-001',
    title: '测试作品',
    description: '作品简介',
    tags: '["标签1", "标签2"]',
    narrative_perspective: 'third_person',
    writing_style: 'natural',
    daily_word_goal: 6000,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-15T00:00:00Z',
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Render helper with router
// ---------------------------------------------------------------------------
function renderDashboard(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <DashboardPage />
    </MemoryRouter>
  )
}

describe('DashboardPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockProjects = []
    mockLoading = false
    mockNeedsSetup = false
    mockFetchProjects.mockResolvedValue(undefined)
    mockCreateProject.mockResolvedValue(makeProject({ id: 'new-1', title: '新作品' }))
    mockUpdateProject.mockResolvedValue(makeProject({ id: 'edit-1', title: '已编辑' }))
    mockDeleteProject.mockResolvedValue(true)
    mockApiPostForm.mockReset()
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/novel-creation/sessions') {
        return Promise.resolve({ data: { data: { sessions: [] } } })
      }
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
  })

  it('opens the existing import flow when routed from the creation chooser', async () => {
    renderDashboard('/dashboard?create=import')

    expect(await screen.findByRole('dialog', { name: '导入外部小说' })).toBeInTheDocument()
    expect(screen.getByText('只导入文稿')).toBeInTheDocument()
  })

  // ------------------------------------------------------------------
  // TC-F01: Renders empty state when no projects
  // ------------------------------------------------------------------
  it('should render empty state when no projects exist', async () => {
    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('作品库还是空的。使用上方“创建新作品”开始立项，或直接导入已有小说。')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------
  // TC-F02: Renders "create" prompt button in empty state
  // ------------------------------------------------------------------
  it('should keep project-package and external-novel imports as separate actions', async () => {
    renderDashboard()

    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: /创建新作品/ })).toHaveLength(1)
      expect(screen.getAllByRole('button', { name: /直接创建/ })).toHaveLength(1)
      expect(screen.getAllByRole('button', { name: /导入外部小说/ })).toHaveLength(1)
      expect(screen.getAllByRole('button', { name: /导入司命项目包/ })).toHaveLength(1)
    })
  })

  // ------------------------------------------------------------------
  // TC-F03: Renders project cards when projects exist
  // ------------------------------------------------------------------
  it('should render project cards when projects exist', async () => {
    mockProjects = [
      makeProject({ id: '1', title: '修仙传' }),
      makeProject({ id: '2', title: '魔法世界' }),
    ]

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('修仙传')).toBeInTheDocument()
      expect(screen.getByText('魔法世界')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------
  // TC-F04: Shows loading spinner while fetching
  // ------------------------------------------------------------------
  it('should show loading spinner while fetching projects', () => {
    mockLoading = true

    renderDashboard()

    expect(screen.getByText('正在载入作品...')).toBeInTheDocument()
  })

  // ------------------------------------------------------------------
  // TC-F05: Fetches projects on mount
  // ------------------------------------------------------------------
  it('should fetch projects on component mount', async () => {
    renderDashboard()

    await waitFor(() => {
      expect(mockFetchProjects).toHaveBeenCalled()
    })
  })

  // ------------------------------------------------------------------
  // TC-F06: Search triggers fetchProjects with keyword
  // ------------------------------------------------------------------
  it('should trigger search on search button click', async () => {
    const user = userEvent.setup()
    renderDashboard()

    const searchInput = screen.getByPlaceholderText('搜索作品标题或简介')
    await user.type(searchInput, '修仙')

    const searchButton = screen.getByRole('button', { name: /搜索/ })
    await user.click(searchButton)

    expect(mockFetchProjects).toHaveBeenCalledWith('修仙')
  })

  // ------------------------------------------------------------------
  // TC-F07: Search shows "no match" message when empty results
  // ------------------------------------------------------------------
  it('should show "no match" message when search has no results', async () => {
    mockProjects = []
    renderDashboard()

    const searchInput = screen.getByPlaceholderText('搜索作品标题或简介')
    fireEvent.change(searchInput, { target: { value: '不存在' } })

    const searchButton = screen.getByRole('button', { name: /搜索/ })
    fireEvent.click(searchButton)

    await waitFor(() => {
      expect(screen.getByText('没有找到匹配的作品')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------
  // TC-F08: Create button opens the dedicated creation workbench
  // ------------------------------------------------------------------
  it('should navigate to the creation workbench when "创建新作品" button is clicked', async () => {
    const user = userEvent.setup()
    renderDashboard()

    const createButton = screen.getByText('创建新作品')
    await user.click(createButton)

    expect(mockNavigate).toHaveBeenCalledWith('/novel-creation')
  })

  // ------------------------------------------------------------------
  // TC-F09: Create modal form submission
  // ------------------------------------------------------------------
  it('should submit create form and navigate to new project', async () => {
    const user = userEvent.setup()
    mockCreateProject.mockResolvedValueOnce(
      makeProject({ id: 'created-123', title: '我的新作品' })
    )
    renderDashboard()

    await user.click(screen.getAllByText('直接创建')[0])

    const titleInput = screen.getByLabelText('作品标题')
    await user.type(titleInput, '我的新作品')

    const submitButton = screen.getByRole('button', { name: /^创建$/ })
    await user.click(submitButton)

    await waitFor(() => {
      expect(mockCreateProject).toHaveBeenCalledWith(
        expect.objectContaining({ title: '我的新作品' })
      )
      expect(mockNavigate).toHaveBeenCalledWith('/project/created-123')
    })
  })

  // ------------------------------------------------------------------
  // TC-F10: Create modal can be cancelled
  // ------------------------------------------------------------------
  it('should close create modal on cancel', async () => {
    const user = userEvent.setup()
    renderDashboard()

    await user.click(screen.getAllByText('直接创建')[0])

    const footer = document.querySelector('.ant-modal-footer') as HTMLElement
    const cancelButton = within(footer).getByRole('button', { name: '取消' })
    await user.click(cancelButton)

    await waitFor(() => {
      expect(screen.queryByLabelText('作品标题')).not.toBeInTheDocument()
    })
  })

  it('should atomically import Markdown through the external-novel route', async () => {
    const user = userEvent.setup()
    mockApiPostForm.mockResolvedValueOnce({
      data: { data: { project_id: 'markdown-project', total: 2 } },
    })
    renderDashboard()

    await user.click(screen.getByRole('button', { name: /导入外部小说/ }))
    const dialog = await screen.findByRole('dialog', { name: '导入外部小说' })
    const input = dialog.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['# 第一章\n正文'], '小说.md', { type: 'text/markdown' })
    await user.upload(input, file)
    await user.click(within(dialog).getByRole('button', { name: '创建作品并导入' }))

    await waitFor(() => {
      expect(mockApiPostForm).toHaveBeenCalledTimes(1)
      expect(mockApiPostForm.mock.calls[0][0]).toBe('/import/project-file')
      expect((mockApiPostForm.mock.calls[0][1] as FormData).get('file')).toBe(file)
      expect(mockNavigate).toHaveBeenCalledWith('/project/markdown-project')
    })
  })

  it('should reuse the project-package idempotency key when retrying', async () => {
    const user = userEvent.setup()
    mockApiPostForm
      .mockRejectedValueOnce(new Error('暂时失败'))
      .mockResolvedValueOnce({
        data: {
          data: {
            project_id: 'package-project',
            project_title: '恢复作品',
            replayed: true,
          },
        },
      })
    renderDashboard()

    await user.click(screen.getByRole('button', { name: /导入司命项目包/ }))
    const dialog = await screen.findByRole('dialog', { name: '导入司命项目包' })
    const input = dialog.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['PK'], '作品.siming-project', {
      type: 'application/vnd.siming.project+zip',
    })
    await user.upload(input, file)
    const submit = within(dialog).getByRole('button', { name: '创建新作品' })
    await user.click(submit)
    await waitFor(() => expect(mockApiPostForm).toHaveBeenCalledTimes(1))
    await user.click(submit)

    await waitFor(() => {
      expect(mockApiPostForm).toHaveBeenCalledTimes(2)
      const firstConfig = mockApiPostForm.mock.calls[0][2] as { headers: Record<string, string> }
      const secondConfig = mockApiPostForm.mock.calls[1][2] as { headers: Record<string, string> }
      expect(firstConfig.headers['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/i)
      expect(secondConfig.headers['Idempotency-Key']).toBe(
        firstConfig.headers['Idempotency-Key'],
      )
      expect(mockNavigate).toHaveBeenCalledWith('/project/package-project')
    })
  })

  // ------------------------------------------------------------------
  // TC-F11: Edit modal opens with pre-filled data
  // ------------------------------------------------------------------
  it('should open edit modal when edit button clicked', async () => {
    const user = userEvent.setup()
    mockProjects = [makeProject({ id: '1', title: '修仙传', description: '修仙小说', tags: '["玄幻"]' })]

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('修仙传')).toBeInTheDocument()
    })

    const editBtn = document.querySelector('.anticon-edit')?.closest('button')
    if (editBtn) {
      await user.click(editBtn)
    }

    await waitFor(() => {
      expect(screen.getByText('编辑作品')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------
  // TC-F12: Delete triggers confirmation and calls deleteProject
  // ------------------------------------------------------------------
  it('should trigger delete when confirmed in popconfirm', async () => {
    const user = userEvent.setup()
    mockProjects = [makeProject({ id: 'del-1', title: '待删除作品' })]
    const modalConfirmSpy = vi.spyOn(Modal, 'confirm').mockImplementation((config) => {
      config.onOk?.()
      return {
        destroy: vi.fn(),
        update: vi.fn(),
      } as unknown as ReturnType<typeof Modal.confirm>
    })

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('待删除作品')).toBeInTheDocument()
    })

    const deleteBtn = screen.getByRole('button', { name: '删除 待删除作品' })
    await user.click(deleteBtn)

    await waitFor(() => {
      expect(modalConfirmSpy).toHaveBeenCalledWith(expect.objectContaining({
        title: '确认删除作品',
        okText: '删除',
      }))
      expect(mockDeleteProject).toHaveBeenCalledWith('del-1')
    })
    modalConfirmSpy.mockRestore()
  })

  it('should guide an unconfigured first-time user to the free setup flow', async () => {
    mockNeedsSetup = true

    renderDashboard()

    expect(await screen.findByText('想让 AI 一起创作？先完成一次免费准备')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /免费准备 AI/ })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: /创建新作品/ })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: /免费准备 AI/ }))
    expect(mockNavigate).toHaveBeenCalledWith('/getting-started')
  })

  // ------------------------------------------------------------------
  // TC-F13: Clicking project card navigates to project workspace
  // ------------------------------------------------------------------
  it('should navigate to project workspace when card is clicked', async () => {
    const user = userEvent.setup()
    mockProjects = [makeProject({ id: 'nav-1', title: '导航测试' })]

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('导航测试')).toBeInTheDocument()
    })

    const card = screen.getByText('导航测试').closest('.ant-card')
    if (card) {
      await user.click(card)
    }

    expect(mockNavigate).toHaveBeenCalledWith('/project/nav-1')
  })

  // ------------------------------------------------------------------
  // TC-F14: Tags are rendered from JSON string
  // ------------------------------------------------------------------
  it('should render tags from JSON string', async () => {
    mockProjects = [makeProject({ id: 't1', title: '标签作品', tags: '["玄幻", "修仙", "热血"]' })]

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('玄幻')).toBeInTheDocument()
      expect(screen.getByText('修仙')).toBeInTheDocument()
      expect(screen.getByText('热血')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------
  // TC-F15: Empty description shows placeholder text
  // ------------------------------------------------------------------
  it('should show placeholder text when description is empty', async () => {
    mockProjects = [makeProject({ id: 'd1', title: '无简介作品', description: null })]

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('暂无简介')).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------
  // TC-F16: Date formatting is displayed
  // ------------------------------------------------------------------
  it('should display formatted dates', async () => {
    mockProjects = [
      makeProject({
        id: 'date1',
        title: '日期作品',
        created_at: '2024-06-15T08:00:00Z',
        updated_at: '2025-01-20T12:00:00Z',
      }),
    ]

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText(/2025/)).toBeInTheDocument()
    })
  })
})
