import { useParams, useNavigate } from 'react-router-dom'
import { Button, Layout, Menu } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { useAppStore } from '../stores'
import {
  BarChartOutlined,
  BookOutlined,
  BranchesOutlined,
  ExportOutlined,
  FileAddOutlined,
  GlobalOutlined,
  TeamOutlined,
  ThunderboltOutlined,
  ApartmentOutlined,
  SettingOutlined,
  HomeOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from '@ant-design/icons'
import WorldbuildingPage from './WorldbuildingPage'
import CharactersPage from './CharactersPage'
import OutlinePage from './OutlinePage'
import WriterPage from './WriterPage'
import StatsPage from './StatsPage'
import ExportPage from './ExportPage'
import DeconstructPage from './DeconstructPage'
import VisualizationPage from './VisualizationPage'
import ImportPage from './ImportPage'

const { Sider, Content } = Layout

type MenuKey = 'world' | 'characters' | 'outline' | 'writer' | 'export' | 'stats' | 'deconstruct' | 'visualization' | 'import' | 'settings' | 'dashboard'

function ProjectWorkspace() {
  const { projectId } = useParams<{ projectId: string }>()
  const [activeKey, setActiveKey] = useState<MenuKey>('writer')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const navigate = useNavigate()
  const { projects, getProject } = useAppStore()

  const projectTitle = useMemo(() => {
    if (!projectId) return ''
    const cached = projects.find((p) => p.id === projectId)
    if (cached) return cached.title
    return ''
  }, [projectId, projects])

  useEffect(() => {
    if (projectId && !projectTitle) {
      getProject(projectId)
    }
  }, [projectId, projectTitle, getProject])

  const menuItems = [
    { key: 'writer', icon: <BookOutlined />, label: '写作工作台' },
    { key: 'outline', icon: <BranchesOutlined />, label: '大纲规划' },
    { key: 'characters', icon: <TeamOutlined />, label: '角色管理' },
    { key: 'world', icon: <GlobalOutlined />, label: '世界观' },
    { key: 'stats', icon: <BarChartOutlined />, label: '统计追踪' },
    { key: 'deconstruct', icon: <ThunderboltOutlined />, label: '拆书分析' },
    { key: 'import', icon: <FileAddOutlined />, label: '内容导入' },
    { key: 'visualization', icon: <ApartmentOutlined />, label: '可视化' },
    { key: 'export', icon: <ExportOutlined />, label: '导出' },
    { type: 'divider' as const },
    { key: 'dashboard', icon: <HomeOutlined />, label: '作品管理' },
    { key: 'settings', icon: <SettingOutlined />, label: '系统设置' },
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        width={180}
        collapsedWidth={48}
        collapsible
        collapsed={sidebarCollapsed}
        onCollapse={setSidebarCollapsed}
        trigger={null}
        theme="light"
        style={{ borderRight: '1px solid #f0f0f0' }}
      >
        <div
          style={{
            alignItems: 'center',
            borderBottom: '1px solid #f0f0f0',
            display: 'flex',
            fontWeight: 'bold',
            gap: 8,
            justifyContent: sidebarCollapsed ? 'center' : 'space-between',
            minHeight: 56,
            padding: sidebarCollapsed ? 8 : 16,
          }}
        >
          {!sidebarCollapsed && <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>作品: {projectTitle || (projectId ? projectId.slice(0, 8) + '...' : '')}</span>}
          <Button
            type="text"
            size="small"
            icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setSidebarCollapsed((value) => !value)}
          />
        </div>
        <Menu
          mode="inline"
          inlineCollapsed={sidebarCollapsed}
          selectedKeys={[activeKey]}
          onClick={({ key }) => {
            if (key === 'dashboard') {
              navigate('/dashboard')
              return
            }
            if (key === 'settings') {
              navigate('/settings', { state: { fromProjectId: projectId } })
              return
            }
            setActiveKey(key as MenuKey)
          }}
          items={menuItems}
        />
      </Sider>
      <Content style={{ padding: 24, background: '#fff' }}>
        {activeKey === 'world' && projectId ? (
          <WorldbuildingPage projectId={projectId} />
        ) : activeKey === 'characters' && projectId ? (
          <CharactersPage projectId={projectId} />
        ) : activeKey === 'outline' && projectId ? (
          <OutlinePage projectId={projectId} />
        ) : activeKey === 'writer' && projectId ? (
          <WriterPage projectId={projectId} />
        ) : activeKey === 'stats' && projectId ? (
          <StatsPage projectId={projectId} />
        ) : activeKey === 'export' && projectId ? (
          <ExportPage projectId={projectId} />
        ) : activeKey === 'deconstruct' && projectId ? (
          <DeconstructPage projectId={projectId} />
        ) : activeKey === 'visualization' && projectId ? (
          <VisualizationPage projectId={projectId} />
        ) : activeKey === 'import' && projectId ? (
          <ImportPage projectId={projectId} />
        ) : (
        <div style={{ fontSize: 18, color: '#999', textAlign: 'center', marginTop: 100 }}>
          选择左侧菜单开始
        </div>
        )}
      </Content>
    </Layout>
  )
}

export default ProjectWorkspace
