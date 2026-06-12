/**
 * Desktop control-panel assistant.
 *
 * It runs outside the project workspace page, but still binds every chat to a
 * real project so tool calls can read and write novel data safely.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Empty,
  Input,
  InputNumber,
  Popover,
  Select,
  Space,
  Spin,
  Switch,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  DeleteOutlined,
  ExportOutlined,
  FolderOpenOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
  SettingOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { useModelOptions } from '../hooks/useModelOptions'
import './GuiAssistantChat.css'

const { Title, Paragraph, Text } = Typography

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface Project {
  id: string
  title: string
  updated_at?: string
}

interface Conversation {
  id: string
  project_id?: string
  title: string
  scope?: string
  message_count?: number
  created_at?: string
  updated_at?: string
}

interface PersistedMessage {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string
  status?: string
  payload?: { reply?: string } | null
  created_at?: string
}

interface ChatMessage {
  id?: string
  role: 'user' | 'assistant'
  content: string
  status?: string
  created_at?: string
}

type AssistantMode = 'fast' | 'quality'

const PROJECT_STORAGE_KEY = 'moshu.gui.assistant.projectId'
const SIDEBAR_STORAGE_KEY = 'moshu.gui.assistant.sidebarCollapsed'

function GuiAssistantChat() {
  const [projects, setProjects] = useState<Project[]>([])
  const [activeProjectId, setActiveProjectId] = useState<string>()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConvId, setActiveConvId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [inputValue, setInputValue] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [loading, setLoading] = useState(false)
  const [projectsLoading, setProjectsLoading] = useState(false)
  const [conversationsLoading, setConversationsLoading] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem(SIDEBAR_STORAGE_KEY) === '1')
  const [assistantMode, setAssistantMode] = useState<AssistantMode>('fast')
  const [temperature, setTemperature] = useState(0.3)
  const [maxTokens, setMaxTokens] = useState<number | null>(null)
  const [autoApply, setAutoApply] = useState(true)

  const { modelOptions, defaultModel, loading: modelsLoading } = useModelOptions()
  const [model, setModel] = useState<string | undefined>()

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const activeProject = useMemo(
    () => projects.find((project) => project.id === activeProjectId),
    [projects, activeProjectId],
  )

  useEffect(() => {
    if (!model && defaultModel) setModel(defaultModel)
  }, [model, defaultModel])

  useEffect(() => {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, sidebarCollapsed ? '1' : '0')
  }, [sidebarCollapsed])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const fetchProjects = useCallback(async () => {
    setProjectsLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<{ items: Project[]; total: number }>>('/projects')
      const items = res.data?.data?.items || []
      setProjects(items)

      const savedProjectId = localStorage.getItem(PROJECT_STORAGE_KEY) || undefined
      const nextProject = items.find((item) => item.id === savedProjectId) || items[0]
      if (nextProject) {
        setActiveProjectId((current) => current || nextProject.id)
        localStorage.setItem(PROJECT_STORAGE_KEY, nextProject.id)
      }
    } catch (err: any) {
      message.error(err.message || '加载作品失败')
    } finally {
      setProjectsLoading(false)
    }
  }, [])

  const fetchConversations = useCallback(async (projectId = activeProjectId) => {
    if (!projectId) {
      setConversations([])
      return []
    }
    setConversationsLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<{ items: Conversation[]; total: number }>>(
        `/projects/${projectId}/ai/assistant/conversations`,
        { scope: 'project' },
      )
      const items = res.data?.data?.items || []
      setConversations(items)
      return items
    } catch {
      setConversations([])
      return []
    } finally {
      setConversationsLoading(false)
    }
  }, [activeProjectId])

  const fetchMessages = useCallback(async (convId: string) => {
    if (!activeProjectId) return
    setLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<{ conversation: Conversation; messages: PersistedMessage[] }>>(
        `/projects/${activeProjectId}/ai/assistant/conversations/${convId}`,
      )
      const loadedMessages = (res.data?.data?.messages || []).map((item) => ({
        id: item.id,
        role: item.role,
        content: item.content || item.payload?.reply || '',
        status: item.status,
        created_at: item.created_at,
      }))
      setMessages(loadedMessages)
      setActiveConvId(res.data.data.conversation.id)
    } catch (err: any) {
      message.error(err.message || '加载对话失败')
    } finally {
      setLoading(false)
    }
  }, [activeProjectId])

  useEffect(() => {
    fetchProjects()
  }, [fetchProjects])

  useEffect(() => {
    if (!activeProjectId) return
    localStorage.setItem(PROJECT_STORAGE_KEY, activeProjectId)
    setActiveConvId(null)
    setMessages([])
    fetchConversations(activeProjectId).then((items) => {
      if (items[0]) fetchMessages(items[0].id)
    })
  }, [activeProjectId, fetchConversations, fetchMessages])

  const startNewConversation = () => {
    abortRef.current?.abort()
    setStreaming(false)
    setActiveConvId(null)
    setMessages([])
    setInputValue('')
  }

  const deleteConversation = async (convId: string) => {
    if (!activeProjectId) return
    try {
      await apiClient.delete(`/projects/${activeProjectId}/ai/assistant/conversations/${convId}`)
      setConversations((prev) => prev.filter((item) => item.id !== convId))
      if (activeConvId === convId) startNewConversation()
      message.success('对话已删除')
    } catch (err: any) {
      message.error(err.message || '删除对话失败')
    }
  }

  const openHomeInBrowser = async () => {
    try {
      const res = await apiClient.post<ApiResponse<{ url: string }>>('/system/open-home')
      message.success(`已在默认浏览器打开：${res.data.data.url}`)
    } catch (err: any) {
      message.error(err.message || '打开默认浏览器失败')
    }
  }

  const appendAssistantText = (text: string, replace = false) => {
    setMessages((prev) => {
      const next = [...prev]
      const last = next[next.length - 1]
      if (last?.role === 'assistant') {
        last.content = replace ? text : `${last.content}${text}`
        last.status = 'running'
      }
      return [...next]
    })
  }

  const upsertConversation = (conversation?: Conversation | null) => {
    if (!conversation) return
    setConversations((prev) => {
      const without = prev.filter((item) => item.id !== conversation.id)
      return [conversation, ...without]
    })
  }

  const handleSseEvent = (event: any) => {
    if (event.type === 'conversation') {
      const conversation = event.conversation as Conversation
      setActiveConvId(conversation.id)
      upsertConversation(conversation)
      return
    }
    if (event.type === 'status') {
      appendAssistantText(`\n${event.message || '正在执行...'}\n`, true)
      return
    }
    if (event.type === 'thinking_delta') {
      appendAssistantText(event.delta || '')
      return
    }
    if (event.type === 'thinking') {
      appendAssistantText(event.content || '', true)
      return
    }
    if (event.type === 'tool') {
      const name = event.tool || 'tool'
      const detail = event.detail || event.message || ''
      appendAssistantText(`\n[${name}] ${detail}\n`)
      return
    }
    if (event.type === 'complete') {
      const reply = event.data?.reply || '已完成。'
      setMessages((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last?.role === 'assistant') {
          last.content = reply
          last.status = 'completed'
        }
        return [...next]
      })
      upsertConversation(event.data?.conversation)
      return
    }
    if (event.type === 'error') {
      throw new Error(event.message || event.detail || 'AI助手执行失败')
    }
  }

  const sendMessage = async () => {
    const text = inputValue.trim()
    if (!text || streaming) return
    if (!activeProjectId) {
      message.warning('请先创建或选择一个作品，AI 助手需要作品上下文才能读写章节、大纲、角色和世界观。')
      return
    }

    const history = messages.slice(-8).map((item) => ({
      role: item.role,
      content: item.content,
    }))

    setMessages((prev) => [
      ...prev,
      { role: 'user', content: text, status: 'completed', created_at: new Date().toISOString() },
      { role: 'assistant', content: '正在分析需求...', status: 'running', created_at: new Date().toISOString() },
    ])
    setInputValue('')
    setStreaming(true)

    try {
      abortRef.current = new AbortController()
      const res = await fetch(`/api/v1/projects/${activeProjectId}/ai/workspace-assistant/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scope: 'project',
          message: text,
          conversation_id: activeConvId || undefined,
          model: model || defaultModel || undefined,
          assistant_mode: assistantMode,
          temperature,
          max_tokens: maxTokens || undefined,
          auto_apply: autoApply,
          outline_batch_count: 3,
          history,
        }),
        signal: abortRef.current.signal,
      })

      if (!res.ok || !res.body) throw new Error(`请求失败：${res.status}`)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const frames = buffer.split(/\r?\n\r?\n/)
        buffer = frames.pop() || ''
        for (const frame of frames) {
          const data = frame
            .split(/\r?\n/)
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.replace(/^data:\s?/, ''))
            .join('\n')
          if (!data || data === '[DONE]') continue
          handleSseEvent(JSON.parse(data))
        }
      }

      buffer += decoder.decode()
      if (buffer.trim()) {
        const data = buffer
          .split(/\r?\n/)
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.replace(/^data:\s?/, ''))
          .join('\n')
        if (data && data !== '[DONE]') handleSseEvent(JSON.parse(data))
      }
      fetchConversations(activeProjectId)
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last?.role === 'assistant') {
            last.content = err.message || '发送失败'
            last.status = 'error'
          }
          return [...next]
        })
        message.error(err.message || '发送失败')
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }

  const stopGeneration = () => {
    abortRef.current?.abort()
    setStreaming(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const settingsContent = (
    <Space direction="vertical" size={12} style={{ width: 300 }}>
      <div>
        <Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>作品上下文</Text>
        <Select
          showSearch
          value={activeProjectId}
          onChange={setActiveProjectId}
          options={projects.map((project) => ({ value: project.id, label: project.title }))}
          loading={projectsLoading}
          placeholder="选择作品"
          optionFilterProp="label"
          style={{ width: '100%' }}
        />
      </div>
      <div>
        <Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>模型</Text>
        <Select
          allowClear
          showSearch
          value={model}
          onChange={setModel}
          options={modelOptions}
          loading={modelsLoading}
          optionFilterProp="label"
          placeholder={modelOptions.length ? '选择模型' : '请先在系统设置里配置模型'}
          style={{ width: '100%' }}
        />
      </div>
      <div>
        <Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>助手模式</Text>
        <Select
          value={assistantMode}
          onChange={setAssistantMode}
          options={[
            { value: 'fast', label: '快速（默认）' },
            { value: 'quality', label: '质量' },
          ]}
          style={{ width: '100%' }}
        />
      </div>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Text type="secondary">自动执行工具</Text>
        <Switch checked={autoApply} onChange={setAutoApply} />
      </Space>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Text type="secondary">温度</Text>
        <InputNumber
          min={0}
          max={2}
          step={0.1}
          value={temperature}
          onChange={(value) => setTemperature(Number(value ?? 0.3))}
          style={{ width: 110 }}
        />
      </Space>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Text type="secondary">最大输出</Text>
        <InputNumber
          min={256}
          step={256}
          value={maxTokens ?? undefined}
          placeholder="默认"
          onChange={(value) => setMaxTokens(value ? Number(value) : null)}
          style={{ width: 110 }}
        />
      </Space>
    </Space>
  )

  return (
    <div className={`gui-chat${sidebarCollapsed ? ' gui-chat-collapsed' : ''}`}>
      <aside className="gui-chat-sidebar">
        <div className="gui-chat-sidebar-head">
          {!sidebarCollapsed && (
            <Title level={5} style={{ margin: 0 }}>
              <RobotOutlined /> 对话列表
            </Title>
          )}
          <Space>
            {!sidebarCollapsed && (
              <>
                <Tooltip title="刷新对话">
                  <Button icon={<ReloadOutlined />} size="small" onClick={() => fetchConversations()} loading={conversationsLoading} />
                </Tooltip>
                <Tooltip title="新对话">
                  <Button type="primary" icon={<PlusOutlined />} size="small" onClick={startNewConversation} />
                </Tooltip>
              </>
            )}
            <Tooltip title={sidebarCollapsed ? '展开对话列表' : '收起对话列表'}>
              <Button
                icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                size="small"
                onClick={() => setSidebarCollapsed((value) => !value)}
              />
            </Tooltip>
          </Space>
        </div>
        {!sidebarCollapsed && (
          <div className="gui-chat-conv-list">
            {conversations.length === 0 && !conversationsLoading ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无对话" style={{ padding: '40px 0' }} />
            ) : (
              conversations.map((conv) => (
                <div
                  key={conv.id}
                  className={`gui-chat-conv-item${conv.id === activeConvId ? ' gui-chat-conv-item-active' : ''}`}
                  onClick={() => fetchMessages(conv.id)}
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
        )}
      </aside>

      <main className="gui-chat-main">
        <div className="gui-chat-header">
          <div>
            <Title level={5} style={{ margin: 0 }}>
              {activeConvId ? conversations.find((c) => c.id === activeConvId)?.title || 'AI 助手' : 'AI 助手'}
            </Title>
            <Text type="secondary" className="gui-chat-project-line">
              <FolderOpenOutlined /> {activeProject ? activeProject.title : '未选择作品'}
            </Text>
          </div>
          <Space>
            <Button icon={<ExportOutlined />} onClick={openHomeInBrowser}>
              浏览器打开首页
            </Button>
            <Popover trigger="click" title="助手设置" content={settingsContent}>
              <Button icon={<SettingOutlined />}>助手设置</Button>
            </Popover>
            <Button icon={<PlusOutlined />} onClick={startNewConversation}>
              新对话
            </Button>
          </Space>
        </div>

        <div className="gui-chat-messages">
          {!activeProjectId && !projectsLoading ? (
            <div className="gui-chat-welcome">
              <div className="gui-chat-welcome-icon">📜</div>
              <Title level={3} style={{ margin: '0 0 8px', fontFamily: "'Noto Serif SC', serif" }}>
                先创建一个作品
              </Title>
              <Paragraph type="secondary" style={{ fontSize: 15, maxWidth: 460, textAlign: 'center' }}>
                AI 助手需要作品上下文，才能读取章节、大纲、角色、世界观，也才能把生成结果写回正确的作品。
              </Paragraph>
              <Button type="primary" icon={<ExportOutlined />} size="large" onClick={openHomeInBrowser}>
                打开墨枢首页
              </Button>
            </div>
          ) : !activeConvId && messages.length === 0 ? (
            <div className="gui-chat-welcome">
              <div className="gui-chat-welcome-icon">📜</div>
              <Title level={3} style={{ margin: '0 0 8px', fontFamily: "'Noto Serif SC', serif" }}>
                墨枢 AI 助手
              </Title>
              <Paragraph type="secondary" style={{ fontSize: 15, maxWidth: 460, textAlign: 'center' }}>
                当前绑定作品：{activeProject?.title || '未选择'}。可以直接提需求，助手会自动检索资料、调用工具并写回作品。
              </Paragraph>
              <Button type="primary" icon={<PlusOutlined />} size="large" onClick={startNewConversation}>
                开始新对话
              </Button>
            </div>
          ) : loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
              <Spin size="large" />
            </div>
          ) : (
            <>
              {messages.map((msg, index) => (
                <div key={msg.id || `${msg.role}-${index}`} className={`gui-chat-msg gui-chat-msg-${msg.role}`}>
                  <div className="gui-chat-msg-role">{msg.role === 'user' ? '你' : '墨枢'}</div>
                  <div className="gui-chat-msg-content">
                    {msg.content || (streaming && msg.role === 'assistant' ? '思考中...' : '')}
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        <div className="gui-chat-composer">
          <Input.TextArea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入消息...（Enter 发送，Shift+Enter 换行）"
            autoSize={{ minRows: 2, maxRows: 6 }}
            disabled={streaming || !activeProjectId}
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
                disabled={!inputValue.trim() || !activeProjectId}
              >
                发送
              </Button>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}

export default GuiAssistantChat
