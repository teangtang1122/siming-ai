import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../../shared/api/client'
import type { ApiEnvelope, GettingStartedStatus } from '../../shared/api/contracts'

export const onboardingKeys = {
  all: ['getting-started'] as const,
  summary: () => [...onboardingKeys.all, 'summary'] as const,
  detail: () => [...onboardingKeys.all, 'detail'] as const,
}

export async function getGettingStartedStatus(summary = false, refresh = false) {
  const response = await apiClient.get<ApiEnvelope<GettingStartedStatus>>(
    '/config/getting-started',
    summary || refresh ? { summary, refresh } : undefined,
  )
  return response.data.data
}

export function useGettingStartedSummary(enabled = true) {
  return useQuery({
    queryKey: onboardingKeys.summary(),
    queryFn: () => getGettingStartedStatus(true),
    staleTime: 30_000,
    enabled,
  })
}

export function useGettingStartedStatus() {
  return useQuery({
    queryKey: onboardingKeys.detail(),
    queryFn: async () => {
      const summary = await getGettingStartedStatus(true)
      return summary.needs_setup ? getGettingStartedStatus(false) : summary
    },
    staleTime: 10_000,
  })
}
