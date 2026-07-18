import type {
  OperationAttention,
  OperationOutcome,
  OperationResult,
} from '../../components/interaction'
import type { OperationRun } from '../../shared/api/contracts'

function valueOrUndefined<T>(value: T | null | undefined): T | undefined {
  return value == null ? undefined : value
}

export function toInteractionProjection(operation: OperationRun): {
  outcome?: OperationOutcome
  attention?: OperationAttention
  result?: OperationResult
} {
  const attention = operation.attention
    ? {
        kind: valueOrUndefined(operation.attention.kind),
        title: valueOrUndefined(operation.attention.title),
        message: valueOrUndefined(operation.attention.message),
        action_label: valueOrUndefined(operation.attention.action_label),
        action_url: valueOrUndefined(operation.attention.action_url),
        blocking: valueOrUndefined(operation.attention.blocking),
      }
    : undefined
  const result = operation.result
    ? {
        outcome: valueOrUndefined(operation.result.outcome),
        summary: valueOrUndefined(operation.result.summary),
        completed: operation.result.completed || [],
        incomplete: operation.result.incomplete || [],
        warnings: operation.result.warnings || [],
      }
    : undefined
  return {
    outcome: valueOrUndefined(operation.outcome),
    attention,
    result,
  }
}
