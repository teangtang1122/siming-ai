import { afterEach, describe, expect, it, vi } from 'vitest'

import { runCreationAgentTurn, type CreationAgentTurnEvent } from '../services/novelCreationAgent'

function sseResponse(events: CreationAgentTurnEvent[]) {
  const chunks = events.map((event) => new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`))
  let index = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => index < chunks.length
          ? { done: false, value: chunks[index++] }
          : { done: true, value: undefined },
        releaseLock: vi.fn(),
      }),
    },
    json: async () => ({}),
  }
}

describe('creation Agent SSE client', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('delivers conversation context events as typed state frames before completion', async () => {
    const events: CreationAgentTurnEvent[] = [
      {
        client_turn_id: 'turn-context',
        sequence: 1,
        type: 'conversation_context',
        message: '正在整理较早上下文',
        data: { context_state: { status: 'compressing', latest_checkpoint_id: 'checkpoint-1' } },
      },
      {
        client_turn_id: 'turn-context',
        sequence: 2,
        type: 'conversation_checkpoint',
        message: '较早上下文已整理',
        data: { checkpoint: { id: 'checkpoint-1', status: 'ready' } },
      },
      {
        client_turn_id: 'turn-context',
        sequence: 3,
        type: 'complete',
        message: '本轮完成',
        data: { reply: '已继续', turn_persisted: true },
      },
    ]
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse(events)))
    const received: CreationAgentTurnEvent[] = []

    const result = await runCreationAgentTurn('session-1', '继续', undefined, {
      clientTurnId: 'turn-context',
      onEvent: (event) => received.push(event),
    })

    expect(result.reply).toBe('已继续')
    expect(received.map((event) => event.type)).toEqual([
      'conversation_context',
      'conversation_checkpoint',
      'complete',
    ])
    expect(received[1].data?.checkpoint).toEqual({ id: 'checkpoint-1', status: 'ready' })
  })

  it('reconnects with the same id and last sequence without duplicating events', async () => {
    const requests: Array<Record<string, unknown>> = []
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const request = JSON.parse(String(init?.body || '{}')) as Record<string, unknown>
      requests.push(request)
      if (requests.length === 1) {
        return sseResponse([{
          client_turn_id: 'turn-1',
          sequence: 1,
          type: 'turn_started',
          message: '已接收',
          data: {},
        }])
      }
      return sseResponse([
        {
          client_turn_id: 'turn-1',
          sequence: 2,
          type: 'reply_delta',
          message: '',
          data: { delta: '完成' },
        },
        {
          client_turn_id: 'turn-1',
          sequence: 3,
          type: 'complete',
          message: '本轮完成',
          data: { reply: '完成', turn_persisted: true },
        },
      ])
    })
    vi.stubGlobal('fetch', fetchMock)
    const received: number[] = []

    const result = await runCreationAgentTurn('session-1', '继续', undefined, {
      clientTurnId: 'turn-1',
      onEvent: (event) => received.push(event.sequence),
    })

    expect(result.reply).toBe('完成')
    expect(received).toEqual([1, 2, 3])
    expect(requests).toHaveLength(2)
    expect(requests[0]).toMatchObject({ client_turn_id: 'turn-1', after_sequence: 0 })
    expect(requests[1]).toMatchObject({ client_turn_id: 'turn-1', after_sequence: 1 })
  })

  it('keeps the exact author message separate from typed data-only reference material', async () => {
    const requests: Array<Record<string, unknown>> = []
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      requests.push(JSON.parse(String(init?.body || '{}')) as Record<string, unknown>)
      return sseResponse([{
        client_turn_id: 'turn-reference',
        sequence: 1,
        type: 'complete',
        message: '本轮完成',
        data: { reply: '完成', turn_persisted: true },
      }])
    })
    vi.stubGlobal('fetch', fetchMock)

    await runCreationAgentTurn('session-1', '总结', undefined, {
      clientTurnId: 'turn-reference',
      referenceContext: {
        source_kind: 'attachment',
        source_name: '灰港.txt',
        content: '林野来到灰港。',
        coverage: 'full',
        source_chars: 7,
      },
    })

    expect(requests[0]).toMatchObject({
      message: '总结',
      reference_context: {
        source_kind: 'attachment',
        source_name: '灰港.txt',
        content: '林野来到灰港。',
        coverage: 'full',
        source_chars: 7,
      },
    })
    expect(String(requests[0].message)).not.toContain('林野来到灰港')
  })

  it('retries an initial network connection failure with the same id', async () => {
    const requests: Array<Record<string, unknown>> = []
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      requests.push(JSON.parse(String(init?.body || '{}')) as Record<string, unknown>)
      if (requests.length === 1) throw new TypeError('network disconnected')
      return sseResponse([{
        client_turn_id: 'turn-network',
        sequence: 1,
        type: 'complete',
        message: '本轮完成',
        data: { reply: '已恢复', turn_persisted: true },
      }])
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await runCreationAgentTurn('session-1', '继续', undefined, {
      clientTurnId: 'turn-network',
    })

    expect(result.reply).toBe('已恢复')
    expect(requests).toHaveLength(2)
    expect(requests[0]).toMatchObject({ client_turn_id: 'turn-network', after_sequence: 0 })
    expect(requests[1]).toMatchObject({ client_turn_id: 'turn-network', after_sequence: 0 })
  })

  it('surfaces a terminal SSE error without silently falling back or retrying', async () => {
    const fetchMock = vi.fn(async () => sseResponse([{
      client_turn_id: 'turn-error',
      sequence: 1,
      type: 'error',
      message: 'revision conflict',
      data: { error_type: 'revision_conflict' },
    }]))
    vi.stubGlobal('fetch', fetchMock)

    const error = await runCreationAgentTurn('session-1', '修改', undefined, {
      clientTurnId: 'turn-error',
    }).catch((caught: unknown) => caught)

    expect(error).toMatchObject({
      message: 'revision conflict',
      detail: { error_type: 'revision_conflict' },
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
