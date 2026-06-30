/**
 * Desktop GUI control panel — opened by the native exe window.
 * Contains Settings, External Agent / MCP, AI Chat, and Terminal.
 * Uses its own layout (no workspace sidebar).
 */
import { useState } from 'react'
import { Button, Layout, Menu, Tooltip, Typography, message } from 'antd'
import {
  ApiOutlined,
  BookOutlined,
  CodeOutlined,
  ExportOutlined,
  HddOutlined,
  RobotOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import SettingsPage from './SettingsPage'
import ExternalAgentPage from './ExternalAgentPage'
import GuiAssistantChat from '../components/GuiAssistantChat'
import TerminalPage from './TerminalPage'
import ModelCenterPage from './ModelCenterPage'
import TabCache from '../components/TabCache'
import ThemeSwitcher from '../themes/ThemeSwitcher'

const { Sider, Content } = Layout
const { Title } = Typography

type GuiTab = 'settings' | 'external-agent' | 'ai-chat' | 'models' | 'terminal'

const MENU_ITEMS = [
  { key: 'models', icon: <HddOutlined />, label: '本地 AI' },
  { key: 'ai-chat', icon: <RobotOutlined />, label: 'AI 助手' },
  { key: 'settings', icon: <SettingOutlined />, label: '系统设置' },
  { key: 'external-agent', icon: <ApiOutlined />, label: '外部 Agent' },
  { key: 'terminal', icon: <CodeOutlined />, label: '终端日志' },
]

const TAB_RENDERERS = {
  models: () => <ModelCenterPage embedded />,
  'ai-chat': () => <GuiAssistantChat />,
  settings: () => <SettingsPage embedded />,
  'external-agent': () => <ExternalAgentPage embedded />,
  terminal: () => <TerminalPage />,
}

function GuiPage() {
  const [activeTab, setActiveTab] = useState<GuiTab>('ai-chat')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  const openHomeInBrowser = async () => {
    try {
      const res = await apiClient.post<{ data: { url: string } }>('/system/open-home')
      message.success(`已在浏览器打开写作台：${res.data.data.url}`)
    } catch (err: any) {
      message.error(err.message || '打开浏览器失败')
    }
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        width={200}
        collapsedWidth={56}
        collapsible
        collapsed={sidebarCollapsed}
        onCollapse={setSidebarCollapsed}
        trigger={null}
        theme="light"
        style={{
          borderRight: '1px solid var(--ant-color-border-secondary)',
          transition: 'width 0.25s cubic-bezier(0.22, 1, 0.36, 1)',
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        {/* Logo area */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: sidebarCollapsed ? 'center' : 'flex-start',
            padding: sidebarCollapsed ? '16px 0' : '16px 16px',
            borderBottom: '1px solid var(--ant-color-border-secondary)',
            gap: 10,
            minHeight: 56,
            background: 'linear-gradient(180deg, rgba(0,0,0,0.015) 0%, transparent 100%)',
            cursor: 'pointer',
            transition: 'padding 0.25s ease',
          }}
          onClick={() => setSidebarCollapsed((v) => !v)}
        >
          <BookOutlined style={{ fontSize: 22, lineHeight: 1, color: 'var(--ant-color-primary)' }} />
          {!sidebarCollapsed && (
            <Title
              level={5}
              style={{
                margin: 0,
                fontFamily: "'Noto Serif SC', serif",
                letterSpacing: '0.05em',
                whiteSpace: 'nowrap',
              }}
            >
              司命
            </Title>
          )}
        </div>

        {/* Navigation menu */}
        <Menu
          mode="inline"
          selectedKeys={[activeTab]}
          onClick={({ key }) => setActiveTab(key as GuiTab)}
          items={MENU_ITEMS}
          inlineCollapsed={sidebarCollapsed}
          style={{
            borderRight: 0,
            paddingTop: 8,
          }}
        />

        {/* Bottom actions — open home + theme switcher */}
        <div
          style={{
            position: 'absolute',
            bottom: 0,
            left: 0,
            right: 0,
            borderTop: '1px solid var(--ant-color-border-secondary)',
            padding: sidebarCollapsed ? '10px 6px' : '10px 12px',
            display: 'flex',
            flexDirection: 'column',
            gap: 6,
          }}
        >
          {sidebarCollapsed ? (
            <>
              <Tooltip title="打开写作台" placement="right">
                <Button
                  type="text"
                  icon={<ExportOutlined />}
                  size="small"
                  aria-label="打开写作台"
                  onClick={openHomeInBrowser}
                  style={{ width: '100%' }}
                />
              </Tooltip>
              <Tooltip title="切换主题" placement="right">
                <ThemeSwitcher iconOnly />
              </Tooltip>
            </>
          ) : (
            <>
              <Button
                icon={<ExportOutlined />}
                size="small"
                onClick={openHomeInBrowser}
                style={{ width: '100%' }}
              >
                打开写作台
              </Button>
              <ThemeSwitcher />
            </>
          )}
        </div>
      </Sider>

      <Content
        style={{
          background: 'var(--ant-color-bg-layout)',
          overflow: 'auto',
          height: '100vh',
        }}
      >
        <TabCache activeKey={activeTab} tabs={TAB_RENDERERS} />
      </Content>
    </Layout>
  )
}

export default GuiPage
