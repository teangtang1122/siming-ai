import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getNarrativeDashboard,
  restoreNarrativeCheckpoint,
  updateNarrativeStatus,
} from './api'

export const narrativeKeys = {
  all: (projectId: string) => ['narrative-governance', projectId] as const,
  dashboard: (projectId: string, view: string) => (
    [...narrativeKeys.all(projectId), view] as const
  ),
}

export function useNarrativeDashboard(projectId: string, view: string) {
  return useQuery({
    queryKey: narrativeKeys.dashboard(projectId, view),
    queryFn: () => getNarrativeDashboard(projectId, view),
  })
}

export function useUpdateNarrativeStatus(projectId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { type: string; id: string; status: string }) => (
      updateNarrativeStatus(projectId, input.type, input.id, input.status)
    ),
    onSuccess: () => client.invalidateQueries({ queryKey: narrativeKeys.all(projectId) }),
  })
}

export function useRestoreNarrativeCheckpoint(projectId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (checkpointId: string) => restoreNarrativeCheckpoint(projectId, checkpointId),
    onSuccess: () => client.invalidateQueries({ queryKey: narrativeKeys.all(projectId) }),
  })
}
