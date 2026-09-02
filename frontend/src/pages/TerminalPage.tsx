/**
 * Terminal / Log viewer page for the desktop GUI.
 * Shows the backend runtime log (logs/siming.log, falling back to
 * logs/launcher.log) and provides quick actions like opening the home in a browser.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Space, Switch, Typography, message } from 'antd'
import {
  ClearOutlined,
  ExportOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import './TerminalPage.css'

const { Text } = Typography

interface LogResponse {
  path: string
  content: string
  lines: number
  total?: number
}

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

function TerminalPage() {
  const [logContent, setLogContent] = useState<string>('')
  const [logPath, setLogPath] = useState<string>('')
  const [logLines, setLogLines] = useState(0)
  const [logTotal, setLogTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const termRef = useRef<HTMLPreElement>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchLogs = useCallback(async () => {
    try {
      const res = await apiClient.get<ApiResponse<LogResponse>>('/system/logs', { lines: 300 })
      const data = res.data?.data
      if (data) {
        setLogContent(data.content || '(空)')
        setLogPath(data.path || '')
        setLogLines(data.lines || 0)
        setLogTotal(data.total || 0)
      }
    } catch {
      // silent — don't spam errors on auto-refresh
    }
  }, [])

  const fetchLogsManual = useCallback(async () => {
    setLoading(true)
    try {
      await fetchLogs()
    } finally {
      setLoading(false)
    }
  }, [fetchLogs])

  useEffect(() => {
    fetchLogs()
  }, [fetchLogs])

  useEffect(() => {
    if (autoRefresh) {
      intervalRef.current = setInterval(fetchLogs, 3000)
    } else if (intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [autoRefresh, fetchLogs])

  // Auto-scroll to bottom on new content
  useEffect(() => {
    if (termRef.current && autoRefresh) {
      termRef.current.scrollTop = termRef.current.scrollHeight
    }
  }, [logContent, autoRefresh])

  const openHomeInBrowser = async () => {
    try {
      const res = await apiClient.post<ApiResponse<{ url: string }>>('/system/open-home')
      message.success(`已在浏览器打开写作台：${res.data.data.url}`)
    } catch (err: any) {
      message.error(err.message || '打开默认浏览器失败')
    }
  }

  const setAutoRefreshWithImmediateFeedback = (checked: boolean) => {
    setAutoRefresh(checked)
    if (checked) void fetchLogs()
  }

  const clearCurrentView = () => {
    setAutoRefresh(false)
    setLogContent('')
    setLogLines(0)
    message.info('已清空当前视图并暂停自动刷新；原日志文件未删除')
  }

  return (
    <div className="terminal-page">
      <div className="terminal-toolbar">
        <div className="terminal-heading">
          <Text strong className="terminal-title">终端日志</Text>
          {logPath && (
            <Text type="secondary" className="terminal-log-path" title={logPath}>
              {logPath} · 最近 {logLines}/{logTotal} 行
            </Text>
          )}
        </div>
        <Space size={8} wrap className="terminal-controls">
          <span className="terminal-auto-refresh">
            <Text type="secondary" className="terminal-auto-refresh-label">自动刷新</Text>
            <Switch
              size="small"
              checked={autoRefresh}
              aria-label="自动刷新日志"
              onChange={setAutoRefreshWithImmediateFeedback}
            />
          </span>
          <Button
            icon={<ReloadOutlined />}
            size="small"
            onClick={fetchLogsManual}
            loading={loading}
          >
            刷新
          </Button>
          <Button
            icon={<ClearOutlined />}
            size="small"
            aria-label="清空当前日志视图"
            onClick={clearCurrentView}
          >
            清空视图
          </Button>
          <Button
            type="primary"
            icon={<ExportOutlined />}
            size="small"
            onClick={openHomeInBrowser}
          >
            打开写作台
          </Button>
        </Space>
      </div>

      <pre
        ref={termRef}
        className="terminal-output"
      >
        {logContent || (
          <span className="terminal-empty-state">
            {loading ? '正在加载日志...' : '暂无日志输出'}
          </span>
        )}
        {autoRefresh && (
          <span className="terminal-cursor" />
        )}
      </pre>
    </div>
  )
}

export default TerminalPage
