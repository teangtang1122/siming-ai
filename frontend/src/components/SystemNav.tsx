import { useNavigate, useLocation } from 'react-router-dom'
import { Button, Space } from 'antd'
import { HomeOutlined, SettingOutlined, ApiOutlined } from '@ant-design/icons'

interface SystemNavProps {
  /** Highlight the current page */
  current?: 'dashboard' | 'settings' | 'external-agent'
}

/**
 * Shared navigation bar for system-level pages:
 * Dashboard (作品管理), Settings (系统设置), External Agent (外部 Agent / MCP)
 */
function SystemNav({ current }: SystemNavProps) {
  const navigate = useNavigate()
  const location = useLocation()

  // Auto-detect current page if not specified
  const active = current || (() => {
    if (location.pathname === '/dashboard' || location.pathname === '/') return 'dashboard'
    if (location.pathname === '/settings') return 'settings'
    if (location.pathname === '/external-agent') return 'external-agent'
    return ''
  })()

  return (
    <Space size={8} style={{ marginBottom: 16 }}>
      <Button
        type={active === 'dashboard' ? 'primary' : 'default'}
        icon={<HomeOutlined />}
        onClick={() => navigate('/dashboard')}
      >
        作品管理
      </Button>
      <Button
        type={active === 'settings' ? 'primary' : 'default'}
        icon={<SettingOutlined />}
        onClick={() => navigate('/settings')}
      >
        系统设置
      </Button>
      <Button
        type={active === 'external-agent' ? 'primary' : 'default'}
        icon={<ApiOutlined />}
        onClick={() => navigate('/external-agent')}
      >
        外部 Agent / MCP
      </Button>
    </Space>
  )
}

export default SystemNav
