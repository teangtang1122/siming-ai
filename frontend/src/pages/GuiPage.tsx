/**
 * Desktop GUI control panel — opened by the native exe window.
 * Contains Settings, External Agent / MCP, AI Chat, and Terminal.
 * Uses its own layout (no workspace sidebar).
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Layout, Menu, Tooltip, Typography } from 'antd'
import {
  ApiOutlined,
  BookOutlined,
  CodeOutlined,
  HddOutlined,
  HomeOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RobotOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import SettingsPage from './SettingsPage'
import ExternalAgentPage from './ExternalAgentPage'
import GuiAssistantChat from '../components/GuiAssistantChat'
import TerminalPage from './TerminalPage'
import ModelCenterPage from './ModelCenterPage'
import TabCache from '../components/TabCache'
import ThemeSwitcher from '../themes/ThemeSwitcher'
import './GuiPage.css'

const { Sider, Content } = Layout
const { Title } = Typography

type GuiTab = 'settings' | 'external-agent' | 'ai-chat' | 'models' | 'terminal'

const MENU_ITEMS = [
  {
    type: 'group' as const,
    label: '创作入口',
    children: [{ key: 'ai-chat', icon: <RobotOutlined />, label: 'AI 助手' }],
  },
  {
    type: 'group' as const,
    label: '连接与能力',
    children: [
      { key: 'models', icon: <HddOutlined />, label: '模型与训练' },
      { key: 'external-agent', icon: <ApiOutlined />, label: '外部 Agent' },
    ],
  },
  {
    type: 'group' as const,
    label: '系统',
    children: [
      { key: 'settings', icon: <SettingOutlined />, label: '系统设置' },
      { key: 'terminal', icon: <CodeOutlined />, label: '运行日志' },
    ],
  },
]

const TAB_RENDERERS = {
  models: () => <ModelCenterPage embedded />,
  'ai-chat': () => <GuiAssistantChat />,
  settings: () => <SettingsPage embedded />,
  'external-agent': () => <ExternalAgentPage embedded />,
  terminal: () => <TerminalPage />,
}

function GuiPage() {
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<GuiTab>('ai-chat')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  return (
    <Layout className="gui-page-shell">
      <Sider
        width={188}
        collapsedWidth={56}
        collapsible
        collapsed={sidebarCollapsed}
        onCollapse={setSidebarCollapsed}
        trigger={null}
        theme="light"
        className="gui-page-sider"
      >
        <div className="gui-page-brand">
          <BookOutlined />
          {!sidebarCollapsed && (
            <div>
              <Title level={5}>司命</Title>
              <span>创作控制台</span>
            </div>
          )}
          <Tooltip title={sidebarCollapsed ? '展开导航' : '收起导航'} placement="right">
            <Button
              type="text"
              size="small"
              icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              aria-label={sidebarCollapsed ? '展开导航' : '收起导航'}
              onClick={() => setSidebarCollapsed((value) => !value)}
            />
          </Tooltip>
        </div>

        {/* Navigation menu */}
        <Menu
          mode="inline"
          selectedKeys={[activeTab]}
          onClick={({ key }) => setActiveTab(key as GuiTab)}
          items={MENU_ITEMS}
          inlineCollapsed={sidebarCollapsed}
          className="gui-page-menu"
        />

        <div className="gui-page-footer">
          {sidebarCollapsed ? (
            <>
              <Tooltip title="进入作品库" placement="right">
                <Button
                  type="text"
                  icon={<HomeOutlined />}
                  size="small"
                  aria-label="进入作品库"
                  onClick={() => navigate('/dashboard')}
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
                type="primary"
                icon={<HomeOutlined />}
                size="small"
                onClick={() => navigate('/dashboard')}
                style={{ width: '100%' }}
              >
                进入作品库
              </Button>
              <ThemeSwitcher />
            </>
          )}
        </div>
      </Sider>

      <Content
        className="gui-page-content"
      >
        <TabCache activeKey={activeTab} tabs={TAB_RENDERERS} />
      </Content>
    </Layout>
  )
}

export default GuiPage
