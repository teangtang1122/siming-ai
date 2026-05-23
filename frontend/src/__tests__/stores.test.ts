/**
 * Test cases for the Zustand store (useAppStore).
 *
 * Covers all async actions: fetchProjects, createProject, updateProject,
 * deleteProject, getProject.
 *
 * Test design:
 *   - Does NOT rely on a running backend.
 *   - All API calls are mocked via vi.mock on the api client.
 *   - Each test clears mocks and resets the store state.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'

// ---------------------------------------------------------------------------
// Mock axios at module level — prevent "Cannot read properties of
// undefined (reading 'config')" in jsdom environment.
// MUST be before any module that indirectly imports axios.
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
    AxiosError: class extends Error {
      config?: unknown
      code?: string
      request?: unknown
      response?: unknown
      isAxiosError = true
      constructor(message?: string) { super(message); this.name = 'AxiosError' }
    },
  }
})

import { useAppStore } from '../stores'

// Mock the API client module
vi.mock('../api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    stream: vi.fn(),
  },
}))

import { apiClient } from '../api/client'

// Helper: create a mock API response (mimics AxiosResponse shape)
function mockApiResponse<T>(data: T, code = 0, message = 'success') {
  return {
    data: { code, message, data },
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {} as any,
  }
}

// Helper: create a minimal project object
function makeProject(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 'proj-001',
    title: '测试作品',
    description: '简介',
    tags: '["标签1"]',
    narrative_perspective: 'third_person',
    writing_style: 'natural',
    short_sentences: false,
    custom_style_prompt: null,
    daily_word_goal: 6000,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-15T00:00:00Z',
    ...overrides,
  }
}

describe('useAppStore', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Reset store to initial state
    useAppStore.setState({
      projects: [],
      currentProject: null,
      loading: false,
      error: null,
    })
  })

  // ------------------------------------------------------------------
  // TC-F14: fetchProjects updates projects state
  // ------------------------------------------------------------------
  describe('fetchProjects', () => {
    it('should fetch projects and update state (no keyword)', async () => {
      const mockProjects = [makeProject({ id: 'p1', title: '作品1' }), makeProject({ id: 'p2', title: '作品2' })]
      vi.mocked(apiClient.get).mockResolvedValueOnce(
        mockApiResponse({ items: mockProjects, total: 2 })
      )

      await useAppStore.getState().fetchProjects()

      const state = useAppStore.getState()
      expect(state.projects).toHaveLength(2)
      expect(state.projects[0].title).toBe('作品1')
      expect(state.projects[1].title).toBe('作品2')
      expect(state.loading).toBe(false)
      expect(state.error).toBeNull()
    })

    it('should fetch projects with search keyword', async () => {
      vi.mocked(apiClient.get).mockResolvedValueOnce(
        mockApiResponse({ items: [], total: 0 })
      )

      await useAppStore.getState().fetchProjects('修仙')

      expect(apiClient.get).toHaveBeenCalledWith('/projects', { q: '修仙' })
    })

    it('should set error state on API failure', async () => {
      vi.mocked(apiClient.get).mockRejectedValueOnce(new Error('网络错误'))

      await useAppStore.getState().fetchProjects()

      const state = useAppStore.getState()
      expect(state.error).toBe('网络错误')
      expect(state.loading).toBe(false)
      expect(state.projects).toHaveLength(0)
    })

    it('should set loading=true while fetching', async () => {
      let resolvePromise: (value: unknown) => void
      const pendingPromise = new Promise((resolve) => {
        resolvePromise = resolve
      })
      vi.mocked(apiClient.get).mockReturnValueOnce(pendingPromise as ReturnType<typeof apiClient.get>)

      const fetchPromise = useAppStore.getState().fetchProjects()

      expect(useAppStore.getState().loading).toBe(true)
      expect(useAppStore.getState().error).toBeNull()

      resolvePromise!(mockApiResponse({ items: [], total: 0 }))
      await fetchPromise

      expect(useAppStore.getState().loading).toBe(false)
    })
  })

  // ------------------------------------------------------------------
  // TC-F15: createProject adds to projects and returns project
  // ------------------------------------------------------------------
  describe('createProject', () => {
    it('should create a project and prepend to state', async () => {
      const newProject = makeProject({ id: 'new-001', title: '新作品' })
      vi.mocked(apiClient.post).mockResolvedValueOnce(mockApiResponse(newProject))

      // Pre-populate with one existing project
      useAppStore.setState({ projects: [makeProject({ id: 'old-001', title: '旧作品' })] })

      const result = await useAppStore.getState().createProject({ title: '新作品' })

      expect(result).not.toBeNull()
      expect(result!.id).toBe('new-001')
      expect(result!.title).toBe('新作品')

      const state = useAppStore.getState()
      expect(state.projects).toHaveLength(2)
      expect(state.projects[0].title).toBe('新作品') // prepended
      expect(state.projects[1].title).toBe('旧作品')
    })

    it('should return null and set error on failure', async () => {
      vi.mocked(apiClient.post).mockRejectedValueOnce(new Error('创建失败'))

      const result = await useAppStore.getState().createProject({ title: '失败作品' })

      expect(result).toBeNull()
      expect(useAppStore.getState().error).toBe('创建失败')
    })
  })

  // ------------------------------------------------------------------
  // TC-F16: updateProject updates project in list
  // ------------------------------------------------------------------
  describe('updateProject', () => {
    it('should update an existing project in the list', async () => {
      const updatedProject = makeProject({ id: 'p1', title: '修改后的标题' })
      vi.mocked(apiClient.put).mockResolvedValueOnce(mockApiResponse(updatedProject))

      useAppStore.setState({
        projects: [
          makeProject({ id: 'p1', title: '原标题' }),
          makeProject({ id: 'p2', title: '其他作品' }),
        ],
      })

      const result = await useAppStore.getState().updateProject('p1', { title: '修改后的标题' })

      expect(result).not.toBeNull()
      expect(result!.title).toBe('修改后的标题')

      const state = useAppStore.getState()
      expect(state.projects[0].title).toBe('修改后的标题')
      expect(state.projects[1].title).toBe('其他作品') // unchanged
    })

    it('should update currentProject if it matches the updated id', async () => {
      const updated = makeProject({ id: 'current', title: '已更新' })
      vi.mocked(apiClient.put).mockResolvedValueOnce(mockApiResponse(updated))

      useAppStore.setState({
        currentProject: makeProject({ id: 'current', title: '当前作品' }),
        projects: [makeProject({ id: 'current', title: '当前作品' })],
      })

      await useAppStore.getState().updateProject('current', { title: '已更新' })

      expect(useAppStore.getState().currentProject?.title).toBe('已更新')
    })
  })

  // ------------------------------------------------------------------
  // TC-F17: deleteProject removes project from list
  // ------------------------------------------------------------------
  describe('deleteProject', () => {
    it('should remove a project from the list', async () => {
      vi.mocked(apiClient.delete).mockResolvedValueOnce(mockApiResponse(null))

      useAppStore.setState({
        projects: [
          makeProject({ id: 'p1', title: '作品1' }),
          makeProject({ id: 'p2', title: '作品2' }),
        ],
      })

      const result = await useAppStore.getState().deleteProject('p1')

      expect(result).toBe(true)
      expect(useAppStore.getState().projects).toHaveLength(1)
      expect(useAppStore.getState().projects[0].id).toBe('p2')
    })

    it('should clear currentProject if the deleted project is active', async () => {
      vi.mocked(apiClient.delete).mockResolvedValueOnce(mockApiResponse(null))

      useAppStore.setState({
        currentProject: makeProject({ id: 'p1' }),
        projects: [makeProject({ id: 'p1' })],
      })

      await useAppStore.getState().deleteProject('p1')

      expect(useAppStore.getState().currentProject).toBeNull()
    })

    it('should return false on failure', async () => {
      vi.mocked(apiClient.delete).mockRejectedValueOnce(new Error('删除失败'))

      const result = await useAppStore.getState().deleteProject('p1')

      expect(result).toBe(false)
      expect(useAppStore.getState().error).toBe('删除失败')
    })
  })

  // ------------------------------------------------------------------
  // TC-F18: getProject returns project data
  // ------------------------------------------------------------------
  describe('getProject', () => {
    it('should return project data for a valid id', async () => {
      const project = makeProject({ id: 'detail-1', title: '详情作品' })
      vi.mocked(apiClient.get).mockResolvedValueOnce(mockApiResponse(project))

      const result = await useAppStore.getState().getProject('detail-1')

      expect(result).not.toBeNull()
      expect(result!.id).toBe('detail-1')
      expect(result!.title).toBe('详情作品')
    })

    it('should return null on failure', async () => {
      vi.mocked(apiClient.get).mockRejectedValueOnce(new Error('未找到'))

      const result = await useAppStore.getState().getProject('bad-id')

      expect(result).toBeNull()
      expect(useAppStore.getState().error).toBe('未找到')
    })
  })

  // ------------------------------------------------------------------
  // Synchronous state setters
  // ------------------------------------------------------------------
  describe('synchronous actions', () => {
    it('setCurrentProject should update currentProject', () => {
      const project = makeProject({ id: 'sync-1' })
      useAppStore.getState().setCurrentProject(project)
      expect(useAppStore.getState().currentProject?.id).toBe('sync-1')
    })

    it('setProjects should replace the projects list', () => {
      const list = [makeProject({ id: 'a' }), makeProject({ id: 'b' })]
      useAppStore.getState().setProjects(list)
      expect(useAppStore.getState().projects).toHaveLength(2)
    })

    it('setLoading should update loading flag', () => {
      useAppStore.getState().setLoading(true)
      expect(useAppStore.getState().loading).toBe(true)
    })

    it('setError should update error message', () => {
      useAppStore.getState().setError('something wrong')
      expect(useAppStore.getState().error).toBe('something wrong')
    })
  })
})
