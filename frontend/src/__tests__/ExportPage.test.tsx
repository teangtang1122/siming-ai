import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const { mockApiGet, mockApiPost } = vi.hoisted(() => ({
  mockApiGet: vi.fn(),
  mockApiPost: vi.fn(),
}))

vi.mock('axios', () => ({
  __esModule: true,
  default: {
    create: vi.fn(() => ({
      get: mockApiGet,
      post: mockApiPost,
      postForm: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
      interceptors: {
        request: { use: vi.fn(), eject: vi.fn() },
        response: { use: vi.fn(), eject: vi.fn() },
      },
    })),
  },
}))

import ExportPage from '../pages/ExportPage'

describe('ExportPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApiGet.mockResolvedValue({
      data: {
        data: {
          chapters: [{ id: 'chapter-1', title: '第一章', word_count: 1200, version: 1 }],
          total_chapters: 1,
          total_words: 1200,
        },
      },
    })
    mockApiPost.mockResolvedValue({
      data: new Blob(['package']),
      headers: {
        'content-disposition': "attachment; filename*=UTF-8''%E4%BD%9C%E5%93%81_%E7%BB%93%E6%9E%84.siming-project",
      },
    })
    Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:test'), configurable: true })
    Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  })

  it('keeps readable manuscript formats separate from the dedicated project package', async () => {
    render(<ExportPage projectId="project-12345678" />)

    expect(await screen.findByText('稿件导出')).toBeInTheDocument()
    expect(screen.getByText('TXT 纯文本')).toBeInTheDocument()
    expect(screen.getByText('Word (.docx)')).toBeInTheDocument()
    expect(screen.getByText('PDF')).toBeInTheDocument()
    expect(screen.getByText('司命项目包')).toBeInTheDocument()
    expect(screen.getByText(/Windows 安装版会弹出“另存为”/)).toBeInTheDocument()
    expect(screen.getByText(/不包含自动任务、对话、RAG 或模型配置/)).toBeInTheDocument()

    fireEvent.click(screen.getByText('仅结构'))
    fireEvent.click(screen.getByRole('button', { name: /导出 \.siming-project/ }))

    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        '/projects/project-12345678/project-package/export?profile=structure',
        undefined,
        { responseType: 'blob' },
      )
    })
    expect(URL.createObjectURL).toHaveBeenCalled()
  })
})
