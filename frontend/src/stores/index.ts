import { create } from 'zustand'
import { apiClient } from '../api/client'

export interface Project {
  id: string
  title: string
  description?: string
  tags?: string
  narrative_perspective: string
  writing_style: string
  forbidden_sentence_patterns?: string | null
  rhetoric_guidelines?: string | null
  short_sentences: boolean
  custom_style_prompt?: string | null
  daily_word_goal: number
  created_at: string
  updated_at: string
}

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface AppState {
  // Current active project
  currentProject: Project | null
  setCurrentProject: (project: Project | null) => void

  // Projects list
  projects: Project[]
  setProjects: (projects: Project[]) => void

  // Loading state
  loading: boolean
  setLoading: (loading: boolean) => void

  // Error state
  error: string | null
  setError: (error: string | null) => void

  // Async actions
  fetchProjects: (keyword?: string) => Promise<void>
  createProject: (payload: Partial<Project>) => Promise<Project | null>
  updateProject: (id: string, payload: Partial<Project>) => Promise<Project | null>
  deleteProject: (id: string) => Promise<boolean>
  getProject: (id: string) => Promise<Project | null>
}

export const useAppStore = create<AppState>((set) => ({
  currentProject: null,
  setCurrentProject: (project) => set({ currentProject: project }),

  projects: [],
  setProjects: (projects) => set({ projects }),

  loading: false,
  setLoading: (loading) => set({ loading }),

  error: null,
  setError: (error) => set({ error }),

  fetchProjects: async (keyword?: string) => {
    set({ loading: true, error: null })
    try {
      const params: Record<string, unknown> = {}
      if (keyword) params.q = keyword
      const res = await apiClient.get<ApiResponse<{ items: Project[]; total: number }>>('/projects', params)
      set({ projects: res.data.data.items, loading: false })
    } catch (err: any) {
      set({ error: err.message || '获取作品列表失败', loading: false })
    }
  },

  createProject: async (payload: Partial<Project>) => {
    set({ loading: true, error: null })
    try {
      const res = await apiClient.post<ApiResponse<Project>>('/projects', payload)
      const newProject = res.data.data
      set((state) => ({
        projects: [newProject, ...state.projects],
        loading: false,
      }))
      return newProject
    } catch (err: any) {
      set({ error: err.message || '创建作品失败', loading: false })
      return null
    }
  },

  updateProject: async (id: string, payload: Partial<Project>) => {
    set({ loading: true, error: null })
    try {
      const res = await apiClient.put<ApiResponse<Project>>(`/projects/${id}`, payload)
      const updated = res.data.data
      set((state) => ({
        projects: state.projects.map((p) => (p.id === id ? updated : p)),
        currentProject: state.currentProject?.id === id ? updated : state.currentProject,
        loading: false,
      }))
      return updated
    } catch (err: any) {
      set({ error: err.message || '更新作品失败', loading: false })
      return null
    }
  },

  deleteProject: async (id: string) => {
    set({ loading: true, error: null })
    try {
      await apiClient.delete<ApiResponse<null>>(`/projects/${id}`)
      set((state) => ({
        projects: state.projects.filter((p) => p.id !== id),
        currentProject: state.currentProject?.id === id ? null : state.currentProject,
        loading: false,
      }))
      return true
    } catch (err: any) {
      set({ error: err.message || '删除作品失败', loading: false })
      return false
    }
  },

  getProject: async (id: string) => {
    try {
      const res = await apiClient.get<ApiResponse<Project>>(`/projects/${id}`)
      return res.data.data
    } catch (err: any) {
      set({ error: err.message || '获取作品详情失败' })
      return null
    }
  },
}))
