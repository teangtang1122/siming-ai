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
  DatabaseOutlined,
  SettingOutlined,
  HomeOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RobotOutlined,
  BulbOutlined,
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
import CatalogingPage from './CatalogingPage'
import SkillsPage from './SkillsPage'
import AiSidePanel from '../components/AiSidePanel'
import WorkspaceAssistantChat from '../components/WorkspaceAssistantChat'
import { AiPanelProvider, useAiPanelContext } from '../contexts/AiPanelContext'
import { useModelOptions } from '../hooks/useModelOptions'
import { usePanelResize } from '../hooks/usePanelResize'

const { Sider, Content } = Layout

type MenuKey = 'world' | 'characters' | 'outline' | 'writer' | 'export' | 'stats' | 'deconstruct' | 'cataloging' | 'visualization' | 'import' | 'settings' | 'dashboard' | 'skills'

function AiPanelColumn() {
  const { projectId } = useParams<{ projectId: string }>()
  const [aiCollapsed, setAiCollapsed] = useState(false)
  const [aiModel, setAiModel] = useState<string | undefined>()
  const { modelOptions, defaultModel, loading: modelsLoading } = useModelOptions()
  const { width: aiWidth, onDragHandleMouseDown: onAiResize, dragging: aiDragging } = usePanelResize({
    initialWidth: Math.min(560, Math.max(280, window.innerWidth * 0.24)),
  })
  const { selectedOutlineNodeId, selectedCharacterId, selectedText, selectedTextChapterId, triggerRefresh } = useAiPanelContext()

  useEffect(() => {
    if (!aiModel && defaultModel) {
      setAiModel(defaultModel)
    }
  }, [aiModel, defaultModel])

  return (
    <>
      {aiCollapsed && (
        <Button
          type="primary"
          icon={<RobotOutlined />}
          style={{
            borderTopRightRadius: 0,
            borderBottomRightRadius: 0,
            bottom: 24,
            padding: '12px 10px',
            position: 'fixed',
            right: 0,
            writingMode: 'vertical-rl',
            zIndex: 100,
          }}
          onClick={() => setAiCollapsed(false)}
        >
          AI 助手
        </Button>
      )}
      <AiSidePanel
        collapsed={aiCollapsed}
        onToggle={() => setAiCollapsed(true)}
        width={aiWidth}
        onResizeHandle={onAiResize}
        dragging={aiDragging}
      >
        <WorkspaceAssistantChat
          projectId={projectId!}
          scope="project"
          selectedOutlineNodeId={selectedOutlineNodeId}
          selectedCharacterId={selectedCharacterId}
          selectedText={selectedText}
          selectedTextChapterId={selectedTextChapterId}
          model={aiModel}
          defaultModel={defaultModel}
          modelOptions={modelOptions}
          modelsLoading={modelsLoading}
          onModelChange={setAiModel}
          onApplied={triggerRefresh}
        />
      </AiSidePanel>
    </>
  )
}

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
    { key: 'cataloging', icon: <DatabaseOutlined />, label: '作品建档' },
    { key: 'skills', icon: <BulbOutlined />, label: '技能管理' },
    { key: 'import', icon: <FileAddOutlined />, label: '内容导入' },
    { key: 'visualization', icon: <ApartmentOutlined />, label: '可视化' },
    { key: 'export', icon: <ExportOutlined />, label: '导出' },
    { type: 'divider' as const },
    { key: 'dashboard', icon: <HomeOutlined />, label: '作品管理' },
    { key: 'settings', icon: <SettingOutlined />, label: '系统设置' },
  ]

  return (
    <AiPanelProvider>
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
        <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
          <Content style={{ padding: 24, background: '#fff', flex: 1, minWidth: 0 }}>
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
            ) : activeKey === 'cataloging' && projectId ? (
              <CatalogingPage projectId={projectId} />
            ) : activeKey === 'visualization' && projectId ? (
              <VisualizationPage projectId={projectId} />
            ) : activeKey === 'import' && projectId ? (
              <ImportPage projectId={projectId} />
            ) : activeKey === 'skills' && projectId ? (
              <SkillsPage projectId={projectId} />
            ) : (
            <div style={{ fontSize: 18, color: '#999', textAlign: 'center', marginTop: 100 }}>
              选择左侧菜单开始
            </div>
            )}
          </Content>
          <AiPanelColumn />
        </div>
      </Layout>
    </AiPanelProvider>
  )
}

export default ProjectWorkspace
