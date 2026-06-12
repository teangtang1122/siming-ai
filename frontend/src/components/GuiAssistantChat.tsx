/**
 * Standalone AI chat panel for the desktop GUI.
 * Global scope — no project context required.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Empty, Input, Select, Space, Spin, Typography, message } from 'antd'
import {
  DeleteOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { useModelOptions } from '../hooks/useModelOptions'
import './GuiAssistantChat.css'

const { Title, Paragraph } = Typography

interface Conversation {
  id: string
  title: string
  created_at: string
}

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

function GuiAssistantChat() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConvId, setActiveConvId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [inputValue, setInputValue] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [loading, setLoading] = useState(false)
  const [conversationsLoading, setConversationsLoading] = useState(false)
  const { modelOptions, defaultModel, loading: modelsLoading } = useModelOptions()
  const [model, setModel] = useState<string | undefined>()

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!model && defaultModel) setModel(defaultModel)
  }, [model, defaultModel])

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  const fetchConversations = useCallback(async () => {
    setConversationsLoading(true)
    try {
      const res = await apiClient.get<any>('/assistant/conversations', { params: { scope: 'global' } })
      setConversations(res.data?.data?.items || [])
    } catch {
      // ignore
    } finally {
      setConversationsLoading(false)
    }
  }, [])

  const fetchMessages = useCallback(async (convId: string) => {
    setLoading(true)
    try {
      const res = await apiClient.get<any>(`/assistant/conversations/${convId}/messages`)
      setMessages(res.data?.data?.items || [])
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchConversations()
  }, [fetchConversations])

  useEffect(() => {
    if (activeConvId) fetchMessages(activeConvId)
    else setMessages([])
  }, [activeConvId, fetchMessages])

  const createConversation = async (): Promise<string | null> => {
    try {
      const res = await apiClient.post<any>('/assistant/conversations', {
        scope: 'global',
        title: '新对话',
      })
      const conv = res.data?.data
      if (conv) {
        setConversations((prev) => [conv, ...prev])
        setActiveConvId(conv.id)
        return conv.id
      }
      return null
    } catch (err: any) {
      message.error(err.message || '创建对话失败')
      return null
    }
  }

  const deleteConversation = async (convId: string) => {
    try {
      await apiClient.delete(`/assistant/conversations/${convId}`)
      setConversations((prev) => prev.filter((c) => c.id !== convId))
      if (activeConvId === convId) {
        setActiveConvId(null)
        setMessages([])
      }
    } catch (err: any) {
      message.error(err.message || '删除对话失败')
    }
  }

  const sendMessage = async () => {
    const text = inputValue.trim()
    if (!text || streaming) return

    const userMsg: ChatMessage = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, userMsg])
    setInputValue('')
    setStreaming(true)

    const assistantMsg: ChatMessage = {
      id: `stream-${Date.now()}`,
      role: 'assistant',
      content: '',
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, assistantMsg])

    try {
      const convId = activeConvId || await createConversation()
      if (!convId) throw new Error('无法创建对话')

      abortRef.current = new AbortController()
      const res = await fetch(`/api/v1/assistant/conversations/${convId}/messages/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text, model }),
        signal: abortRef.current.signal,
      })

      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const reader = res.body?.getReader()
      if (!reader) throw new Error('No reader')

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') break
          try {
            const parsed = JSON.parse(data)
            if (parsed.type === 'token' && parsed.content) {
              setMessages((prev) => {
                const updated = [...prev]
                const last = updated[updated.length - 1]
                if (last.role === 'assistant') {
                  last.content += parsed.content
                }
                return [...updated]
              })
            }
          } catch {
            // skip malformed SSE
          }
        }
      }

      // Refresh conversations to get updated title
      fetchConversations()
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        message.error(err.message || '发送失败')
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }

  const stopGeneration = () => {
    abortRef.current?.abort()
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  return (
    <div className="gui-chat">
      {/* Sidebar: conversation list */}
      <aside className="gui-chat-sidebar">
        <div className="gui-chat-sidebar-head">
          <Title level={5} style={{ margin: 0 }}>
            <RobotOutlined /> 对话列表
          </Title>
          <Space>
            <Button
              icon={<ReloadOutlined />}
              size="small"
              onClick={fetchConversations}
              loading={conversationsLoading}
            />
            <Button
              type="primary"
              icon={<PlusOutlined />}
              size="small"
              onClick={createConversation}
            >
              新建
            </Button>
          </Space>
        </div>
        <div className="gui-chat-conv-list">
          {conversations.length === 0 && !conversationsLoading ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无对话" style={{ padding: '40px 0' }} />
          ) : (
            conversations.map((conv) => (
              <div
                key={conv.id}
                className={`gui-chat-conv-item${conv.id === activeConvId ? ' gui-chat-conv-item-active' : ''}`}
                onClick={() => setActiveConvId(conv.id)}
              >
                <span className="gui-chat-conv-title">{conv.title || '未命名对话'}</span>
                <Button
                  type="text"
                  size="small"
                  icon={<DeleteOutlined />}
                  danger
                  onClick={(e) => {
                    e.stopPropagation()
                    deleteConversation(conv.id)
                  }}
                />
              </div>
            ))
          )}
        </div>
      </aside>

      {/* Main: chat area */}
      <main className="gui-chat-main">
        {/* Header */}
        <div className="gui-chat-header">
          <Title level={5} style={{ margin: 0 }}>
            {conversations.find((c) => c.id === activeConvId)?.title || 'AI 助手'}
          </Title>
          <Space>
            <Select
              value={model}
              onChange={setModel}
              options={modelOptions}
              loading={modelsLoading}
              style={{ width: 200 }}
              size="small"
              placeholder="选择模型"
            />
          </Space>
        </div>

        {/* Messages */}
        <div className="gui-chat-messages">
          {!activeConvId ? (
            <div className="gui-chat-welcome">
              <div className="gui-chat-welcome-icon">📜</div>
              <Title level={3} style={{ margin: '0 0 8px', fontFamily: "'Noto Serif SC', serif" }}>
                墨枢 AI 助手
              </Title>
              <Paragraph type="secondary" style={{ fontSize: 15, maxWidth: 400, textAlign: 'center' }}>
                创作灵感、大纲规划、角色设计、文笔润色——随时为你效劳。
              </Paragraph>
              <Button type="primary" icon={<PlusOutlined />} size="large" onClick={createConversation}>
                开始新对话
              </Button>
            </div>
          ) : loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
              <Spin size="large" />
            </div>
          ) : (
            <>
              {messages.map((msg) => (
                <div key={msg.id} className={`gui-chat-msg gui-chat-msg-${msg.role}`}>
                  <div className="gui-chat-msg-role">
                    {msg.role === 'user' ? '你' : '墨枢'}
                  </div>
                  <div className="gui-chat-msg-content">
                    {msg.content || (streaming && msg.role === 'assistant' ? '思考中...' : '')}
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        {/* Composer */}
        {activeConvId && (
          <div className="gui-chat-composer">
            <Input.TextArea
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
              autoSize={{ minRows: 2, maxRows: 6 }}
              disabled={streaming}
            />
            <div className="gui-chat-composer-actions">
              {streaming ? (
                <Button icon={<StopOutlined />} onClick={stopGeneration} danger>
                  停止生成
                </Button>
              ) : (
                <Button
                  type="primary"
                  icon={<SendOutlined />}
                  onClick={sendMessage}
                  disabled={!inputValue.trim()}
                >
                  发送
                </Button>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

export default GuiAssistantChat
