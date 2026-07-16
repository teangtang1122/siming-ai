import axios, { AxiosError, AxiosInstance, AxiosRequestConfig } from 'axios'

const API_BASE_URL = '/api/v1'
const API_TIMEOUT_MS = 600000

class ApiClient {
  private client: AxiosInstance

  constructor() {
    this.client = axios.create({
      baseURL: API_BASE_URL,
      timeout: API_TIMEOUT_MS,
      headers: {
        'Content-Type': 'application/json',
      },
    })

    this.client.interceptors.response.use(
      (response) => response,
      (error: AxiosError) => {
        if (error.response) {
          const data = error.response.data as {
            code?: number
            message?: string
            detail?: string | { message?: string }
          }
          const detailMessage = typeof data.detail === 'string' ? data.detail : data.detail?.message
          const normalized = new Error(data.message || detailMessage || `请求失败: ${error.response.status}`) as Error & {
            response?: typeof error.response
          }
          // Consumers that understand structured API errors can retain failure
          // classes, runtime diagnostics, and recovery actions.
          normalized.response = error.response
          return Promise.reject(normalized)
        }
        if (error.code === 'ECONNABORTED') {
          return Promise.reject(new Error('请求超时，AI任务可能仍在处理中，请稍后重试或减少文本量'))
        }
        return Promise.reject(new Error('网络错误，请检查后端服务是否运行'))
      }
    )
  }

  get<T>(url: string, params?: Record<string, unknown>) {
    return this.client.get<T>(url, { params })
  }

  post<T>(url: string, data?: unknown, config?: AxiosRequestConfig) {
    return this.client.post<T>(url, data, config)
  }

  put<T>(url: string, data?: unknown) {
    return this.client.put<T>(url, data)
  }

  patch<T>(url: string, data?: unknown) {
    return this.client.patch<T>(url, data)
  }

  delete<T>(url: string) {
    return this.client.delete<T>(url)
  }

  stream(url: string, data: unknown, onMessage: (chunk: string) => void, onError?: (err: Error) => void) {
    const fullUrl = `${API_BASE_URL}${url}`
    fetch(fullUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
      .then((response) => {
        if (!response.ok || !response.body) {
          throw new Error('SSE请求失败')
        }
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        const emitFrame = (frame: string) => {
          const payload = frame
            .split(/\r?\n/)
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.replace(/^data:\s?/, ''))
            .join('\n')
          if (payload && payload !== '[DONE]') {
            onMessage(payload)
          }
        }

        const read = () => {
          reader.read().then(({ done, value }) => {
            if (done) {
              buffer += decoder.decode()
              if (buffer.trim()) emitFrame(buffer)
              return
            }
            buffer += decoder.decode(value, { stream: true })
            const frames = buffer.split(/\r?\n\r?\n/)
            buffer = frames.pop() || ''
            for (const frame of frames) {
              if (frame.trim()) emitFrame(frame)
            }
            read()
          }).catch((err) => {
            onError?.(err)
          })
        }
        read()
      })
      .catch((err) => {
        onError?.(err)
      })
  }
}

export const apiClient = new ApiClient()
