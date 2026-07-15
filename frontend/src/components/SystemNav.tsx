import { useNavigate, useLocation } from 'react-router-dom'
import { Button, Space, Tooltip, Typography } from 'antd'
import { BookOutlined, HomeOutlined, PlusOutlined, RocketOutlined, RobotOutlined, SettingOutlined } from '@ant-design/icons'
import ThemeSwitcher from '../themes/ThemeSwitcher'

const { Text } = Typography

interface SystemNavProps {
  /** Highlight the current page */
  current?: 'dashboard' | 'creation' | 'assistant' | 'getting-started' | 'settings' | 'external-agent'
}

/**
 * Shared navigation bar for system-level pages:
 * Dashboard (作品管理). Settings and External Agent / MCP now live in the desktop GUI.
 */
function SystemNav({ current }: SystemNavProps) {
  const navigate = useNavigate()
  const location = useLocation()

  // Auto-detect current page if not specified
  const active = current || (() => {
    if (location.pathname === '/dashboard' || location.pathname === '/') return 'dashboard'
    if (location.pathname === '/novel-creation') return 'creation'
    if (location.pathname === '/gui') return 'assistant'
    if (location.pathname === '/getting-started') return 'getting-started'
    if (location.pathname === '/settings') return 'settings'
    if (location.pathname === '/external-agent') return 'external-agent'
    return ''
  })()

  const navItems = [
    { key: 'dashboard', label: '作品库', icon: <HomeOutlined />, path: '/dashboard' },
    { key: 'creation', label: '新书立项', icon: <PlusOutlined />, path: '/novel-creation' },
    { key: 'assistant', label: 'AI 助手', icon: <RobotOutlined />, path: '/gui' },
    { key: 'getting-started', label: '快速开始', icon: <RocketOutlined />, path: '/getting-started' },
  ] as const

  return (
    <nav className="system-nav" aria-label="系统导航">
      <button className="system-nav-brand" type="button" aria-label="返回作品库" onClick={() => navigate('/dashboard')}>
        <BookOutlined aria-hidden="true" />
        <Text strong>司命</Text>
      </button>
      <Space size={4} className="system-nav-links">
        {navItems.map((item) => (
          <Tooltip key={item.key} title={item.label}>
            <Button
              type="text"
              icon={item.icon}
              className={active === item.key ? 'system-nav-link-active' : undefined}
              aria-label={item.label}
              aria-current={active === item.key ? 'page' : undefined}
              onClick={() => navigate(item.path)}
            >
              {item.label}
            </Button>
          </Tooltip>
        ))}
      </Space>
      <div className="system-nav-tools">
        <Tooltip title="系统设置">
          <Button
            type="text"
            icon={<SettingOutlined />}
            aria-label="系统设置"
            aria-current={active === 'settings' ? 'page' : undefined}
            onClick={() => navigate('/settings')}
          />
        </Tooltip>
        <ThemeSwitcher />
      </div>
    </nav>
  )
}

export default SystemNav
