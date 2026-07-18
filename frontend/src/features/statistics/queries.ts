import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getStatsHistory, getTodayStats, updateDailyGoal } from './api'

export const statisticsKeys = {
  all: (projectId: string) => ['statistics', projectId] as const,
  today: (projectId: string) => [...statisticsKeys.all(projectId), 'today'] as const,
  history: (projectId: string, days: number) => (
    [...statisticsKeys.all(projectId), 'history', days] as const
  ),
}

export function useTodayStats(projectId: string) {
  return useQuery({
    queryKey: statisticsKeys.today(projectId),
    queryFn: () => getTodayStats(projectId),
  })
}

export function useStatsHistory(projectId: string, days: number) {
  return useQuery({
    queryKey: statisticsKeys.history(projectId, days),
    queryFn: () => getStatsHistory(projectId, days),
  })
}

export function useUpdateDailyGoal(projectId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (dailyWordGoal: number) => updateDailyGoal(projectId, dailyWordGoal),
    onSuccess: () => client.invalidateQueries({ queryKey: statisticsKeys.all(projectId) }),
  })
}
