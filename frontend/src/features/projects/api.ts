import { apiClient } from '../../shared/api/client'
import type {
  ApiEnvelope,
  Project,
  ProjectCreateDraft,
  ProjectCreateInput,
  ProjectListData,
  ProjectUpdateInput,
} from '../../shared/api/contracts'

export async function listProjects(keyword?: string) {
  const params = keyword ? { q: keyword } : undefined
  const response = await apiClient.get<ApiEnvelope<ProjectListData>>('/projects', params)
  return response.data.data
}

export async function getProject(projectId: string) {
  const response = await apiClient.get<ApiEnvelope<Project>>(`/projects/${projectId}`)
  return response.data.data
}

export async function createProject(payload: ProjectCreateDraft) {
  const request: ProjectCreateInput = {
    daily_word_goal: 6000,
    narrative_perspective: 'third_person',
    short_sentences: false,
    writing_style: 'natural',
    ...payload,
  }
  const response = await apiClient.post<ApiEnvelope<Project>>('/projects', request)
  return response.data.data
}

export async function updateProject(projectId: string, payload: ProjectUpdateInput) {
  const response = await apiClient.put<ApiEnvelope<Project>>(`/projects/${projectId}`, payload)
  return response.data.data
}

export async function deleteProject(projectId: string) {
  await apiClient.delete(`/projects/${projectId}`)
}
