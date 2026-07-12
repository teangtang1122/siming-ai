import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

const { mockGet, mockPost, mockDelete, mockNavigate } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockDelete: vi.fn(),
  mockNavigate: vi.fn(),
}))

vi.mock('../api/client', () => ({
  apiClient: { get: mockGet, post: mockPost, delete: mockDelete },
}))

vi.mock('../hooks/useModelOptions', () => ({
  useModelOptions: () => ({ defaultModel: 'openai:test' }),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

import GuiAssistantChat from '../components/GuiAssistantChat'

describe('GuiAssistantChat new-book handoff', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
    mockGet.mockImplementation((url: string) => {
      if (url === '/projects') return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      if (url === '/ai/system-assistant/conversations') return Promise.resolve({ data: { data: { items: [], total: 0 } } })
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })
    mockPost.mockImplementation((url: string) => {
      if (url === '/novel-creation/start') return Promise.resolve({ data: { data: { session_id: 'session-1' } } })
      if (url === '/novel-creation/sessions/session-1/interview/next') {
        return Promise.resolve({ data: { data: { session_id: 'session-1', state: 'ready', history: [] } } })
      }
      if (url === '/novel-creation/sessions/session-1/runs') {
        return Promise.resolve({ data: { data: { run: { id: 'run-1', status: 'running' } } } })
      }
      if (url === '/ai/system-assistant/conversations') {
        return Promise.resolve({ data: { data: { conversation: { id: 'conversation-1', title: '新书' } } } })
      }
      if (url === '/ai/system-assistant/conversations/conversation-1/turns') {
        return Promise.resolve({ data: { data: { conversation: { id: 'conversation-1', title: '新书' } } } })
      }
      return Promise.reject(new Error(`unexpected POST ${url}`))
    })
  })

  it('hands a completed interview to a compact concept run instead of drafting full blueprints', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><GuiAssistantChat /></MemoryRouter>)

    await user.type(await screen.findByPlaceholderText(/输入消息/), '我要创建新的小说')
    await user.click(screen.getByRole('button', { name: /发送/ }))

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/novel-creation?session=session-1&run=run-1&model=openai%3Atest')
    })
    expect(mockPost).toHaveBeenCalledWith('/novel-creation/sessions/session-1/interview/next', expect.objectContaining({
      qa_history: [],
      model: 'openai:test',
    }))
    expect(mockPost).toHaveBeenCalledWith('/novel-creation/sessions/session-1/runs', expect.objectContaining({
      stage: 'concepts',
      operation: 'generate_concepts',
    }))
    expect(mockPost).not.toHaveBeenCalledWith('/novel-creation/draft', expect.anything())
  })
})
