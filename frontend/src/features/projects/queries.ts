import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ProjectCreateDraft, ProjectUpdateInput } from '../../shared/api/contracts'
import { createProject, deleteProject, getProject, listProjects, updateProject } from './api'

export const projectKeys = {
  all: ['projects'] as const,
  lists: () => [...projectKeys.all, 'list'] as const,
  list: (keyword?: string) => [...projectKeys.lists(), keyword || ''] as const,
  details: () => [...projectKeys.all, 'detail'] as const,
  detail: (projectId: string) => [...projectKeys.details(), projectId] as const,
}

export function useProjects(keyword?: string) {
  return useQuery({
    queryKey: projectKeys.list(keyword),
    queryFn: () => listProjects(keyword),
  })
}

export function useProject(projectId?: string) {
  return useQuery({
    queryKey: projectKeys.detail(projectId || ''),
    queryFn: () => getProject(projectId!),
    enabled: Boolean(projectId),
  })
}

export function useCreateProject() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (payload: ProjectCreateDraft) => createProject(payload),
    onSuccess: (project) => {
      client.setQueryData(projectKeys.detail(project.id), project)
      void client.invalidateQueries({ queryKey: projectKeys.lists() })
    },
  })
}

export function useUpdateProject() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ projectId, payload }: { projectId: string; payload: ProjectUpdateInput }) => (
      updateProject(projectId, payload)
    ),
    onSuccess: (project) => {
      client.setQueryData(projectKeys.detail(project.id), project)
      void client.invalidateQueries({ queryKey: projectKeys.lists() })
    },
  })
}

export function useDeleteProject() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: deleteProject,
    onSuccess: (_, projectId) => {
      client.removeQueries({ queryKey: projectKeys.detail(projectId) })
      void client.invalidateQueries({ queryKey: projectKeys.lists() })
    },
  })
}
