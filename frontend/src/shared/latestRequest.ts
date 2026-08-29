export interface LatestRequest<TTarget> {
  generation: number
  target: TTarget
}

export interface LatestRequestGate<TTarget> {
  begin: (target: TTarget) => LatestRequest<TTarget>
  invalidate: () => void
  isCurrent: (request: LatestRequest<TTarget>) => boolean
}

/**
 * Gives write-back ownership to the most recently started UI request.
 *
 * Network cancellation is still useful for saving work, but correctness must not
 * depend on it: a response may already be in flight when the author switches the
 * editor target. Call invalidate() for target changes that do not immediately
 * start another request, such as opening a blank create form or closing a drawer.
 */
export function createLatestRequestGate<TTarget>(): LatestRequestGate<TTarget> {
  let generation = 0

  return {
    begin(target) {
      generation += 1
      return { generation, target }
    },
    invalidate() {
      generation += 1
    },
    isCurrent(request) {
      return request.generation === generation
    },
  }
}
