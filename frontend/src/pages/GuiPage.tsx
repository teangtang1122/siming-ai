/**
 * Desktop GUI control panel — opened by the native exe window.
 * Contains Settings, External Agent / MCP config, and AI Chat.
 * Uses its own layout (no workspace sidebar).
 */
import { useState } from 'react'
import { Layout, Menu, Typography } from 'antd'
import {
  SettingOutlined,
  ApiOutlined,
  RobotOutlined,
} from '@ant-design/icons'
import SettingsPage from './SettingsPage'
import ExternalAgentPage from './ExternalAgentPage'
import GuiAssistantChat from '../components/GuiAssistantChat'
import ThemeSwitcher from '../themes/ThemeSwitcher'

const { Sider, Content } = Layout
const { Title } = Typography

type GuiTab = 'settings' | 'external-agent' | 'ai-chat'

const MENU_ITEMS = [
  { key: 'settings', icon: <SettingOutlined />, label: '系统设置' },
  { key: 'external-agent', icon: <ApiOutlined />, label: '外部 Agent / MCP' },
  { key: 'ai-chat', icon: <RobotOutlined />, label: 'AI 助手' },
]

function GuiPage() {
  const [activeTab, setActiveTab] = useState<GuiTab>('ai-chat')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  const renderContent = () => {
    switch (activeTab) {
      case 'settings':
        return <SettingsPage embedded />
      case 'external-agent':
        return <ExternalAgentPage embedded />
      case 'ai-chat':
        return <GuiAssistantChat />
      default:
        return <SettingsPage />
    }
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        width={200}
        collapsedWidth={64}
        collapsible
        collapsed={sidebarCollapsed}
        onCollapse={setSidebarCollapsed}
        theme="light"
        style={{
          borderRight: '1px solid var(--ant-color-border-secondary)',
          boxShadow: '2px 0 8px rgba(0, 0, 0, 0.03)',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: sidebarCollapsed ? 'center' : 'flex-start',
            padding: sidebarCollapsed ? '20px 0' : '20px 16px',
            borderBottom: '1px solid var(--ant-color-border-secondary)',
            gap: 10,
          }}
        >
          <span style={{ fontSize: 24 }}>📜</span>
          {!sidebarCollapsed && (
            <Title level={5} style={{ margin: 0, fontFamily: "'Noto Serif SC', serif", letterSpacing: '0.05em' }}>
              墨枢
            </Title>
          )}
        </div>
        <Menu
          mode="inline"
          selectedKeys={[activeTab]}
          onClick={({ key }) => setActiveTab(key as GuiTab)}
          items={MENU_ITEMS}
          style={{ borderRight: 0, paddingTop: 8 }}
        />
        <div style={{ position: 'absolute', bottom: 48, left: 0, right: 0, display: 'flex', justifyContent: 'center' }}>
          <ThemeSwitcher />
        </div>
      </Sider>
      <Content
        style={{
          background: 'var(--ant-color-bg-layout)',
          overflow: 'auto',
          height: '100vh',
        }}
      >
        <div
          key={activeTab}
          className="moshu-content-enter"
          style={{ minHeight: '100%' }}
        >
          {renderContent()}
        </div>
      </Content>
    </Layout>
  )
}

export default GuiPage
