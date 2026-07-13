import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { mockPost, mockNavigate } = vi.hoisted(() => ({
  mockPost: vi.fn(),
  mockNavigate: vi.fn(),
}))

vi.mock('../api/client', () => ({
  apiClient: { post: mockPost },
}))

import { useNovelCreationInterviewController } from '../hooks/useNovelCreationInterviewController'

describe('useNovelCreationInterviewController', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('owns the interview state and handoff from the first question through concepts', async () => {
    let interviewCalls = 0
    mockPost.mockImplementation((url: string) => {
      if (url === '/novel-creation/start') {
        return Promise.resolve({ data: { data: { session_id: 'session-1' } } })
      }
      if (url === '/novel-creation/sessions/session-1/interview/next') {
        interviewCalls += 1
        if (interviewCalls === 1) {
          return Promise.resolve({
            data: {
              data: {
                session_id: 'session-1',
                state: 'question',
                question: { question: '主角最害怕失去什么？', type: 'text' },
                history: [],
                runtime: {
                  effective_model: 'codex_cli:codex-cli',
                  provider: 'codex_cli',
                  model_source: 'conversation_override',
                  tool_mode: 'local_cli_text_json',
                  timeout_seconds: 45,
                  quota_status: 'unknown',
                },
              },
            },
          })
        }
        return Promise.resolve({
          data: {
            data: {
              session_id: 'session-1',
              state: 'ready',
              history: [{ question: '主角最害怕失去什么？', answer: '刚刚找回的妹妹' }],
              runtime: {
                effective_model: 'codex_cli:codex-cli',
                provider: 'codex_cli',
                model_source: 'conversation_override',
                tool_mode: 'local_cli_text_json',
                timeout_seconds: 45,
                quota_status: 'unknown',
              },
            },
          },
        })
      }
      if (url === '/novel-creation/sessions/session-1/runs') {
        return Promise.resolve({ data: { data: { run: { id: 'run-1', status: 'running' } } } })
      }
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })

    const { result } = renderHook(() => useNovelCreationInterviewController({
      model: 'codex_cli:codex-cli',
      modelSource: 'conversation_override',
      navigate: mockNavigate,
    }))

    await act(async () => {
      const transition = await result.current.start({ userBrief: '秘密组织培养的实验体逃离异世。' })
      expect(transition.kind).toBe('question')
    })
    expect(result.current.state.sessionId).toBe('session-1')
    expect(result.current.state.activeQuestion?.question).toContain('最害怕')
    expect(result.current.state.runtime.tool_mode).toBe('local_cli_text_json')

    await act(async () => {
      const transition = await result.current.answer('刚刚找回的妹妹')
      expect(transition.kind).toBe('ready')
    })
    expect(result.current.state.questionHistory).toEqual([
      { question: '主角最害怕失去什么？', answer: '刚刚找回的妹妹' },
    ])

    await act(async () => {
      await result.current.handoffToWorkbench()
    })
    expect(mockPost).toHaveBeenCalledWith('/novel-creation/sessions/session-1/runs', {
      stage: 'concepts',
      model: 'codex_cli:codex-cli',
      use_model: true,
      operation: 'generate_concepts',
    })
    expect(mockNavigate).toHaveBeenCalledWith('/novel-creation?session=session-1&run=run-1&model=codex_cli%3Acodex-cli')
  })

  it('keeps a failed skip in the error state and exposes quota recovery metadata', async () => {
    let interviewCalls = 0
    mockPost.mockImplementation((url: string) => {
      if (url === '/novel-creation/start') {
        return Promise.resolve({ data: { data: { session_id: 'session-1' } } })
      }
      if (url === '/novel-creation/sessions/session-1/interview/next') {
        interviewCalls += 1
        if (interviewCalls === 1) {
          return Promise.resolve({
            data: { data: { session_id: 'session-1', state: 'question', question: { question: '开局的代价是什么？', type: 'text' } } },
          })
        }
        return Promise.reject({
          response: {
            data: {
              detail: {
                message: 'Free usage exceeded, retrying in 9h',
                failure_class: 'quota_or_rate_limit',
                next_action: '切换有额度的模型后重试。',
                runtime: {
                  effective_model: 'opencode_cli:free-model',
                  provider: 'opencode_cli',
                  model_source: 'conversation_override',
                  tool_mode: 'local_cli_text_json',
                  timeout_seconds: 45,
                  quota_status: 'exhausted_or_limited',
                  failure_class: 'quota_or_rate_limit',
                },
              },
            },
          },
        })
      }
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })

    const { result } = renderHook(() => useNovelCreationInterviewController({
      model: 'opencode_cli:free-model',
      modelSource: 'conversation_override',
    }))

    await act(async () => {
      await result.current.start({ userBrief: '我要开一本新小说。' })
    })
    await act(async () => {
      const transition = await result.current.skip()
      expect(transition.kind).toBe('error')
    })

    expect(result.current.state.phase).toBe('error')
    expect(result.current.state.activeQuestion).toBeNull()
    expect(result.current.state.runtime.quota_status).toBe('exhausted_or_limited')
    expect(result.current.state.runtime.failure_class).toBe('quota_or_rate_limit')
    expect(result.current.state.error).toContain('Free usage exceeded')
    expect(result.current.state.error).toContain('切换有额度的模型后重试')
  })
})
