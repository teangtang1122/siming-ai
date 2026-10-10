/**
 * Desktop GUI control panel — opened by the native exe window.
 * Contains Settings, External Agent / MCP, AI Chat, and Terminal.
 * Uses its own layout (no workspace sidebar).
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Layout, Menu, Tooltip, Typography } from 'antd'
import {
  ApiOutlined,
  BookOutlined,
  BranchesOutlined,
  CodeOutlined,
  HddOutlined,
  HomeOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RobotOutlined,
  RocketOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import SettingsPage from './SettingsPage'
import ExternalAgentPage from './ExternalAgentPage'
import GuiAssistantChat from '../components/GuiAssistantChat'
import TerminalPage from './TerminalPage'
import ModelCenterPage from './ModelCenterPage'
import TabCache from '../components/TabCache'
import ThemeSwitcher from '../themes/ThemeSwitcher'
import AppVersion from '../components/AppVersion'
import { openContextInspector } from '../shared/contextInspector'
import { GettingStartedPanel } from './GettingStartedPage'
import './GuiPage.css'

const { Sider, Content } = Layout
const { Title } = Typography

type GuiTab = 'settings' | 'external-agent' | 'ai-chat' | 'quick-start' | 'models' | 'terminal'

const COMPACT_NAVIGATION_QUERY = '(max-width: 760px)'

function isCompactNavigation() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia(COMPACT_NAVIGATION_QUERY).matches
}

const MENU_ITEMS = [
  {
    type: 'group' as const,
    label: '创作入口',
    children: [
      { key: 'ai-chat', icon: <RobotOutlined />, label: 'AI 助手' },
      { key: 'quick-start', icon: <RocketOutlined />, label: '快速开始' },
    ],
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
      { key: 'context-inspector', icon: <BranchesOutlined />, label: '调用记录' },
      { key: 'settings', icon: <SettingOutlined />, label: '系统设置' },
      { key: 'terminal', icon: <CodeOutlined />, label: '运行日志' },
    ],
  },
]

const TAB_RENDERERS = {
  models: () => <ModelCenterPage embedded />,
  'ai-chat': () => <GuiAssistantChat />,
  'quick-start': () => (
    <div className="gui-embedded-page">
      <header className="siming-section-header">
        <div>
          <span className="siming-section-kicker">第一次使用</span>
          <Title level={3}><RocketOutlined /> 快速开始</Title>
          <p className="siming-section-description">从服务商官网获取 API Key，连接并测试后开始创作。</p>
        </div>
      </header>
      <GettingStartedPanel />
    </div>
  ),
  settings: () => <SettingsPage embedded />,
  'external-agent': () => <ExternalAgentPage embedded />,
  terminal: () => <TerminalPage />,
}

function GuiPage() {
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<GuiTab>('ai-chat')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [compactNavigation, setCompactNavigation] = useState(isCompactNavigation)

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined

    const mediaQuery = window.matchMedia(COMPACT_NAVIGATION_QUERY)
    const handleChange = (event: MediaQueryListEvent) => setCompactNavigation(event.matches)
    setCompactNavigation(mediaQuery.matches)

    if (typeof mediaQuery.addEventListener === 'function') {
      mediaQuery.addEventListener('change', handleChange)
      return () => mediaQuery.removeEventListener('change', handleChange)
    }

    mediaQuery.addListener(handleChange)
    return () => mediaQuery.removeListener(handleChange)
  }, [])

  const navigationCollapsed = sidebarCollapsed || compactNavigation
  const navigationItems = navigationCollapsed
    ? MENU_ITEMS.flatMap((group) => group.children)
    : MENU_ITEMS

  return (
    <Layout className="gui-page-shell">
      <Sider
        width={188}
        collapsedWidth={56}
        collapsible
        collapsed={navigationCollapsed}
        onCollapse={setSidebarCollapsed}
        trigger={null}
        theme="light"
        className="gui-page-sider"
      >
        <div className={`gui-page-brand${navigationCollapsed ? ' gui-page-brand-collapsed' : ''}`}>
          {(!navigationCollapsed || compactNavigation) && <BookOutlined />}
          {!navigationCollapsed && (
            <div>
              <Title level={5}>司命</Title>
              <span>创作控制台 <AppVersion className="gui-page-version" /></span>
            </div>
          )}
          {!compactNavigation && (
            <Tooltip title={sidebarCollapsed ? '展开导航' : '收起导航'} placement="right">
              <Button
                type="text"
                size="small"
                icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                aria-label={sidebarCollapsed ? '展开导航' : '收起导航'}
                onClick={() => setSidebarCollapsed((value) => !value)}
              />
            </Tooltip>
          )}
        </div>

        {/* Navigation menu */}
        <Menu
          mode="inline"
          selectedKeys={[activeTab]}
          onClick={({ key }) => {
            if (key === 'context-inspector') openContextInspector({})
            else setActiveTab(key as GuiTab)
          }}
          items={navigationItems}
          inlineCollapsed={navigationCollapsed}
          className="gui-page-menu"
        />

        <div className="gui-page-footer">
          {navigationCollapsed ? (
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
