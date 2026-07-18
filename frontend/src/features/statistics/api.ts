import { apiClient } from '../../shared/api/client'
import type { ApiEnvelope } from '../../shared/api/contracts'

export interface TodayStats {
  date: string
  total_words: number
  daily_goal: number
  progress_percent: number
  chapters_written: number
}

export interface DailyStats {
  date: string
  total_words: number
  daily_goal: number
}

export interface HistoryStats {
  items: DailyStats[]
  total_days: number
  total_words: number
  average_words_per_day: number
}

export async function getTodayStats(projectId: string) {
  const response = await apiClient.get<ApiEnvelope<TodayStats>>(
    `/projects/${projectId}/stats/today`,
  )
  return response.data.data
}

export async function getStatsHistory(projectId: string, days: number) {
  const response = await apiClient.get<ApiEnvelope<HistoryStats>>(
    `/projects/${projectId}/stats/history`,
    { days },
  )
  return response.data.data
}

export async function updateDailyGoal(projectId: string, dailyWordGoal: number) {
  const response = await apiClient.put<ApiEnvelope<{ daily_word_goal: number }>>(
    `/projects/${projectId}/stats/goal`,
    { daily_word_goal: dailyWordGoal },
  )
  return response.data.data
}
