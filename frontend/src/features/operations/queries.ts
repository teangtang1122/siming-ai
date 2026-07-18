import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../shared/api/client'
import type { ApiEnvelope, OperationListData, OperationRun } from '../../shared/api/contracts'

export const operationKeys = {
  all: ['operations'] as const,
  list: (limit = 30) => [...operationKeys.all, 'list', limit] as const,
  detail: (operationId: string) => [...operationKeys.all, 'detail', operationId] as const,
}

export async function listOperations(limit = 30) {
  const response = await apiClient.get<ApiEnvelope<OperationListData>>('/operations', { limit })
  return response.data.data.items
}

export function useOperations(limit = 30) {
  return useQuery({
    queryKey: operationKeys.list(limit),
    queryFn: () => listOperations(limit),
    refetchInterval: 3_000,
    staleTime: 1_000,
  })
}

export function useOperationAction(limit = 30) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ operationId, action }: { operationId: string; action: string }) => {
      await apiClient.post(`/operations/${operationId}/${action}`)
    },
    onSuccess: () => client.invalidateQueries({ queryKey: operationKeys.list(limit) }),
  })
}

export function updateOperationInCache(
  current: OperationRun[] | undefined,
  operation: OperationRun,
) {
  if (!current) return [operation]
  const found = current.some((item) => item.id === operation.id)
  return found
    ? current.map((item) => item.id === operation.id ? operation : item)
    : [operation, ...current]
}
