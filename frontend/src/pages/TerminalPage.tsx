/**
 * Terminal / Log viewer page for the desktop GUI.
 * Shows launcher.log output and provides quick actions like opening the home in a browser.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Space, Switch, Typography, message } from 'antd'
import {
  ClearOutlined,
  ExportOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'

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

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Toolbar */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '12px 0',
          borderBottom: '1px solid var(--ant-color-border-secondary)',
          marginBottom: 12,
          flexShrink: 0,
        }}
      >
        <div>
          <Text strong style={{ fontSize: 15 }}>终端日志</Text>
          {logPath && (
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 12 }}>
              {logPath} · {logLines}/{logTotal} 行
            </Text>
          )}
        </div>
        <Space size={8}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>自动刷新</Text>
            <Switch size="small" checked={autoRefresh} onChange={setAutoRefresh} />
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
            onClick={() => {
              setLogContent('')
              setLogLines(0)
            }}
          >
            清屏
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

      {/* Terminal output */}
      <pre
        ref={termRef}
        style={{
          flex: 1,
          margin: 0,
          padding: '12px 16px',
          background: '#16181d',
          color: '#d7dbe2',
          borderRadius: 8,
          fontSize: 12.5,
          lineHeight: 1.7,
          fontFamily: "'Cascadia Code', 'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
          overflow: 'auto',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
          border: '1px solid var(--ant-color-border-secondary)',
        }}
      >
        {logContent || (
          <span style={{ color: '#7e8794', fontStyle: 'italic' }}>
            {loading ? '正在加载日志...' : '暂无日志输出'}
          </span>
        )}
        {autoRefresh && (
          <span style={{ display: 'inline-block', width: 8, height: 15, background: '#79c0ff', marginLeft: 2, animation: 'blink 1s step-end infinite', verticalAlign: 'text-bottom' }} />
        )}
      </pre>

      {/* Inline keyframe for cursor blink */}
      <style>{`
        @keyframes blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
      `}</style>
    </div>
  )
}

export default TerminalPage
