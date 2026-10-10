import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../../shared/api/client'
import type { ApiEnvelope, GettingStartedStatus } from '../../shared/api/contracts'

export const onboardingKeys = {
  all: ['getting-started'] as const,
  status: () => [...onboardingKeys.all, 'status'] as const,
}

export async function getGettingStartedStatus() {
  const response = await apiClient.get<ApiEnvelope<GettingStartedStatus>>(
    '/config/getting-started',
  )
  return response.data.data
}

export function useGettingStartedStatus(enabled = true) {
  return useQuery({
    queryKey: onboardingKeys.status(),
    queryFn: getGettingStartedStatus,
    staleTime: 0,
    refetchOnWindowFocus: true,
    enabled,
  })
}
