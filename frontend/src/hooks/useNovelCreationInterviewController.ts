import { useCallback, useEffect, useMemo, useState } from 'react'
import type { NavigateFunction } from 'react-router-dom'
import { apiClient } from '../api/client'
import { extractNovelInterviewErrorDetail, formatNovelInterviewError } from '../utils/novelInterview'

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface InterviewQuestion {
  question: string
  purpose?: string
  options?: string[]
  type?: 'single_select' | 'multi_select' | 'text'
}

export interface InterviewQuestionAnswer {
  question: string
  answer: string
}

export type InterviewModelSource = 'conversation_override' | 'global_default' | 'task_setting' | 'task_setting_fallback' | 'unconfigured' | 'unknown'
export type InterviewPhase = 'idle' | 'running' | 'question' | 'ready' | 'error'

export interface InterviewRuntime {
  effective_model?: string
  provider?: string
  model_source?: InterviewModelSource
  tool_mode?: string
  timeout_seconds?: number
  quota_status?: 'unknown' | 'available' | 'exhausted_or_limited'
  failure_class?: string
  next_action?: string
}

export interface NovelCreationInterviewState {
  sessionId?: string
  userBrief: string
  activeQuestion?: InterviewQuestion | null
  questionHistory: InterviewQuestionAnswer[]
  phase: InterviewPhase
  runtime: InterviewRuntime
  error?: string
}

export interface InterviewTransition {
  kind: 'question' | 'ready' | 'error'
  state: NovelCreationInterviewState
  error?: string
}

interface InterviewAdvanceData {
  session_id: string
  state: 'question' | 'ready'
  question?: InterviewQuestion | null
  history?: InterviewQuestionAnswer[]
  reason?: string
  skipped?: boolean
  runtime?: InterviewRuntime
}

interface StartSessionData {
  session_id: string
  session?: { id: string }
}

export interface NovelCreationRunSummary {
  id: string
  session_id?: string
  stage: string
  status: string
  current_message?: string
  failure_class?: string
  next_action?: string
  operation_id?: string
  input_revision?: number
  input_snapshot_hash?: string
  model_source?: string
}

interface ConceptRunData {
  run: NovelCreationRunSummary
}

export interface StartNovelCreationSessionInput {
  userBrief: string
  mode?: 'template' | 'internal_llm'
  form?: object
}

export interface NovelCreationSessionResult {
  id: string
  raw: unknown
}

export function buildQuestionAnswerPayload(history: InterviewQuestionAnswer[]) {
  const answers: Record<string, string> = {}
  history.forEach((qa, index) => {
    answers[`qa_${index + 1}: ${qa.question}`] = qa.answer
  })
  return { answers, qa_history: history }
}

export function defaultInterviewRuntime(
  model?: string,
  source: InterviewModelSource = 'unknown',
): InterviewRuntime {
  const [provider] = String(model || '').split(':')
  return {
    effective_model: model || undefined,
    provider: provider || undefined,
    model_source: source,
    tool_mode: 'dynamic_interview_json',
    timeout_seconds: 0,
    quota_status: 'unknown',
  }
}

export function workbenchUrl(sessionId: string, runId?: string, model?: string) {
  const params = new URLSearchParams({ session: sessionId })
  if (runId) params.set('run', runId)
  if (model) params.set('model', model)
  return `/novel-creation?${params.toString()}`
}

export async function startNovelCreationSession(input: StartNovelCreationSessionInput): Promise<NovelCreationSessionResult> {
  const response = await apiClient.post<ApiResponse<StartSessionData>>('/novel-creation/start', {
    mode: input.mode || 'template',
    user_brief: input.userBrief,
    ...(input.form || {}),
  })
  const data = response.data.data
  const id = data.session_id || data.session?.id
  if (!id) throw new Error('新书立项会话未创建成功，请重试。')
  return { id, raw: data }
}

export async function startNovelCreationConceptRun(sessionId: string, model?: string, expectedRevision?: number) {
  const response = await apiClient.post<ApiResponse<ConceptRunData>>(`/novel-creation/sessions/${sessionId}/runs`, {
    stage: 'concepts',
    model,
    use_model: true,
    operation: 'generate_concepts',
    expected_revision: expectedRevision,
  })
  return response.data.data.run
}

function errorTransition(state: NovelCreationInterviewState, error: unknown): InterviewTransition {
  const message = formatNovelInterviewError(error)
  const detail = extractNovelInterviewErrorDetail(error)
  const failureClass = String(detail.failure_class || '')
  const runtime = detail.runtime && typeof detail.runtime === 'object' ? detail.runtime as InterviewRuntime : {}
  const nextState: NovelCreationInterviewState = {
    ...state,
    phase: 'error',
    activeQuestion: null,
    error: message,
    runtime: {
      ...state.runtime,
      ...runtime,
      quota_status: failureClass === 'quota_or_rate_limit' ? 'exhausted_or_limited' : state.runtime.quota_status || 'unknown',
      failure_class: failureClass || state.runtime.failure_class,
    },
  }
  return { kind: 'error', state: nextState, error: message }
}

export function useNovelCreationInterviewController({
  model,
  modelSource = 'unknown',
  navigate,
}: {
  model?: string
  modelSource?: InterviewModelSource
  navigate?: NavigateFunction
}) {
  const requestedModel = modelSource === 'conversation_override' ? model : undefined
  const [state, setState] = useState<NovelCreationInterviewState>({
    userBrief: '',
    questionHistory: [],
    phase: 'idle',
    runtime: defaultInterviewRuntime(model, modelSource),
  })

  useEffect(() => {
    setState((previous) => ({
      ...previous,
      runtime: previous.phase === 'running' || previous.phase === 'question' || previous.phase === 'ready'
        ? previous.runtime
        : defaultInterviewRuntime(model, modelSource),
    }))
  }, [model, modelSource])

  const updateRuntime = useCallback((runtime?: InterviewRuntime) => ({
    ...defaultInterviewRuntime(model, modelSource),
    ...(runtime || {}),
  }), [model, modelSource])

  const commitTransition = useCallback((data: InterviewAdvanceData, base: NovelCreationInterviewState): InterviewTransition => {
    const runtime = updateRuntime(data.runtime)
    const history = Array.isArray(data.history) ? data.history : base.questionHistory
    const nextState: NovelCreationInterviewState = {
      ...base,
      sessionId: data.session_id || base.sessionId,
      activeQuestion: data.state === 'question' ? data.question || null : null,
      questionHistory: history,
      phase: data.state,
      runtime,
      error: undefined,
    }
    return data.state === 'question'
      ? { kind: 'question', state: nextState }
      : { kind: 'ready', state: nextState }
  }, [updateRuntime])

  const requestNext = useCallback(async (
    base: NovelCreationInterviewState,
    history: InterviewQuestionAnswer[],
    skipQuestions = false,
  ): Promise<InterviewTransition> => {
    if (!base.sessionId) {
      const transition = errorTransition(base, new Error('缺少新书立项会话，请重新开始。'))
      setState(transition.state)
      return transition
    }
    const running: NovelCreationInterviewState = {
      ...base,
      activeQuestion: null,
      questionHistory: history,
      phase: 'running',
      error: undefined,
      runtime: updateRuntime(base.runtime),
    }
    setState(running)
    try {
      const response = await apiClient.post<ApiResponse<InterviewAdvanceData>>(
        `/novel-creation/sessions/${base.sessionId}/interview/next`,
        {
          user_brief: base.userBrief,
          qa_history: history,
          skip_questions: skipQuestions,
          ...(requestedModel ? { model: requestedModel } : {}),
        },
        { timeout: 0 },
      )
      const transition = commitTransition(response.data.data, running)
      setState(transition.state)
      return transition
    } catch (error) {
      const transition = errorTransition(running, error)
      setState(transition.state)
      return transition
    }
  }, [commitTransition, requestedModel, updateRuntime])

  const start = useCallback(async (input: StartNovelCreationSessionInput): Promise<InterviewTransition> => {
    const initial: NovelCreationInterviewState = {
      sessionId: undefined,
      userBrief: input.userBrief,
      questionHistory: [],
      activeQuestion: null,
      phase: 'running',
      runtime: defaultInterviewRuntime(model, modelSource),
    }
    setState(initial)
    try {
      const session = await startNovelCreationSession(input)
      return requestNext({ ...initial, sessionId: session.id }, [], false)
    } catch (error) {
      const transition = errorTransition(initial, error)
      setState(transition.state)
      return transition
    }
  }, [model, modelSource, requestNext])

  const answer = useCallback(async (answerText: string): Promise<InterviewTransition> => {
    const answer = String(answerText || '').trim()
    if (!state.activeQuestion || !answer) {
      const transition = errorTransition(state, new Error('请先回答当前问题。'))
      setState(transition.state)
      return transition
    }
    const history = [...state.questionHistory, { question: state.activeQuestion.question, answer }]
    return requestNext({ ...state, questionHistory: history }, history, false)
  }, [requestNext, state])

  const supplement = useCallback(async (text: string): Promise<InterviewTransition> => {
    const answer = String(text || '').trim()
    const history = answer
      ? [...state.questionHistory, { question: '用户补充', answer }]
      : state.questionHistory
    return requestNext({ ...state, questionHistory: history }, history, false)
  }, [requestNext, state])

  const skip = useCallback(async (): Promise<InterviewTransition> => (
    requestNext(state, state.questionHistory, true)
  ), [requestNext, state])

  const replaceHistory = useCallback((history: InterviewQuestionAnswer[]) => {
    setState((previous) => ({ ...previous, questionHistory: history }))
  }, [])

  const replaceQuestion = useCallback((question: InterviewQuestion | null) => {
    setState((previous) => ({
      ...previous,
      activeQuestion: question,
      phase: question ? 'question' : previous.phase === 'question' ? 'idle' : previous.phase,
    }))
  }, [])

  const rerunWithHistory = useCallback(async (history = state.questionHistory): Promise<InterviewTransition> => (
    requestNext({ ...state, questionHistory: history }, history, false)
  ), [requestNext, state])

  const handoffToWorkbench = useCallback(async (sessionId?: string) => {
    const targetSessionId = sessionId || state.sessionId
    if (!targetSessionId) throw new Error('缺少新书立项会话，请重新开始。')
    const run = await startNovelCreationConceptRun(targetSessionId, requestedModel)
    const url = workbenchUrl(targetSessionId, run.id, requestedModel)
    navigate?.(url)
    return { run, url }
  }, [navigate, requestedModel, state.sessionId])

  const adoptSession = useCallback((sessionId: string, userBrief = '', history: InterviewQuestionAnswer[] = []) => {
    setState({
      sessionId,
      userBrief,
      questionHistory: history,
      activeQuestion: null,
      phase: 'idle',
      runtime: defaultInterviewRuntime(model, modelSource),
    })
  }, [model, modelSource])

  const reset = useCallback(() => {
    setState({
      userBrief: '',
      questionHistory: [],
      activeQuestion: null,
      phase: 'idle',
      runtime: defaultInterviewRuntime(model, modelSource),
    })
  }, [model, modelSource])

  return useMemo(() => ({
    state,
    start,
    answer,
    supplement,
    skip,
    rerunWithHistory,
    replaceHistory,
    replaceQuestion,
    handoffToWorkbench,
    adoptSession,
    reset,
  }), [adoptSession, answer, handoffToWorkbench, replaceHistory, replaceQuestion, rerunWithHistory, reset, skip, start, state, supplement])
}
