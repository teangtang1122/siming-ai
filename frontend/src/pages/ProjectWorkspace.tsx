import { useParams, useNavigate } from 'react-router-dom'
import { Button, Layout, Menu, Tooltip } from 'antd'
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
  HomeOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RobotOutlined,
  BulbOutlined,
  ClockCircleOutlined,
  RightOutlined,
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
import { ScheduledTasksPage } from './ScheduledTasksPage'
import AiSidePanel from '../components/AiSidePanel'
import WorkspaceAssistantChat from '../components/WorkspaceAssistantChat'
import { AiPanelProvider, useAiPanelContext } from '../contexts/AiPanelContext'
import { useModelOptions } from '../hooks/useModelOptions'
import { usePanelResize } from '../hooks/usePanelResize'
import ThemeSwitcher from '../themes/ThemeSwitcher'

const { Sider, Content } = Layout

type MenuKey = 'world' | 'characters' | 'outline' | 'writer' | 'export' | 'stats' | 'deconstruct' | 'cataloging' | 'visualization' | 'import' | 'skills' | 'scheduler'

/** Menu key → Chinese page title mapping */
const PAGE_TITLES: Record<MenuKey, string> = {
  writer: '写作工作台',
  outline: '大纲规划',
  characters: '角色管理',
  world: '世界观',
  stats: '统计追踪',
  deconstruct: '拆书分析',
  cataloging: '作品建档',
  visualization: '可视化',
  skills: '技能管理',
  scheduler: '自动任务',
  import: '内容导入',
  export: '导出',
}

function AiPanelColumn({ aiCollapsed, setAiCollapsed }: { aiCollapsed: boolean; setAiCollapsed: (v: boolean) => void }) {
  const { projectId } = useParams<{ projectId: string }>()
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

  if (aiCollapsed) return null

  return (
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
  )
}

function ProjectWorkspace() {
  const { projectId } = useParams<{ projectId: string }>()
  const [activeKey, setActiveKey] = useState<MenuKey>('writer')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [aiCollapsed, setAiCollapsed] = useState(true)
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
    {
      type: 'group' as const,
      label: sidebarCollapsed ? '' : '创作',
      children: [
        { key: 'writer', icon: <BookOutlined />, label: '写作工作台' },
        { key: 'outline', icon: <BranchesOutlined />, label: '大纲规划' },
        { key: 'characters', icon: <TeamOutlined />, label: '角色管理' },
        { key: 'world', icon: <GlobalOutlined />, label: '世界观' },
      ],
    },
    {
      type: 'group' as const,
      label: sidebarCollapsed ? '' : '工具',
      children: [
        { key: 'stats', icon: <BarChartOutlined />, label: '统计追踪' },
        { key: 'deconstruct', icon: <ThunderboltOutlined />, label: '拆书分析' },
        { key: 'cataloging', icon: <DatabaseOutlined />, label: '作品建档' },
        { key: 'visualization', icon: <ApartmentOutlined />, label: '可视化' },
      ],
    },
    {
      type: 'group' as const,
      label: sidebarCollapsed ? '' : '设置',
      children: [
        { key: 'skills', icon: <BulbOutlined />, label: '技能管理' },
        { key: 'scheduler', icon: <ClockCircleOutlined />, label: '自动任务' },
        { key: 'import', icon: <FileAddOutlined />, label: '内容导入' },
        { key: 'export', icon: <ExportOutlined />, label: '导出' },
      ],
    },
  ]

  const renderContent = () => {
    if (!projectId) return null
    switch (activeKey) {
      case 'world': return <WorldbuildingPage projectId={projectId} />
      case 'characters': return <CharactersPage projectId={projectId} />
      case 'outline': return <OutlinePage projectId={projectId} />
      case 'writer': return <WriterPage projectId={projectId} />
      case 'stats': return <StatsPage projectId={projectId} />
      case 'export': return <ExportPage projectId={projectId} />
      case 'deconstruct': return <DeconstructPage projectId={projectId} />
      case 'cataloging': return <CatalogingPage projectId={projectId} />
      case 'visualization': return <VisualizationPage projectId={projectId} />
      case 'import': return <ImportPage projectId={projectId} />
      case 'skills': return <SkillsPage projectId={projectId} />
      case 'scheduler': return <ScheduledTasksPage projectId={projectId} />
      default: return <WriterPage projectId={projectId} />
    }
  }

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
          style={{ borderRight: '1px solid var(--ant-color-border-secondary)' }}
        >
          <div
            style={{
              alignItems: 'center',
              borderBottom: '1px solid var(--ant-color-border-secondary)',
              display: 'flex',
              fontWeight: 'bold',
              gap: 8,
              justifyContent: sidebarCollapsed ? 'center' : 'space-between',
              minHeight: 56,
              padding: sidebarCollapsed ? 8 : 16,
            }}
          >
            {!sidebarCollapsed && (
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 14, fontFamily: "'Noto Serif SC', 'LXGW WenKai', serif" }}>
                {projectTitle || (projectId ? projectId.slice(0, 8) + '...' : '')}
              </span>
            )}
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
            onClick={({ key }) => setActiveKey(key as MenuKey)}
            items={menuItems}
            style={{ borderRight: 0 }}
          />
        </Sider>
        <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0 }}>
          {/* Header bar */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              height: 48,
              padding: '0 24px',
              borderBottom: '1px solid var(--ant-color-border-secondary)',
              background: 'var(--ant-color-bg-layout)',
              flexShrink: 0,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
              <Button
                type="link"
                icon={<HomeOutlined />}
                onClick={() => navigate('/dashboard')}
                style={{ padding: 0, fontSize: 14 }}
              >
                作品管理
              </Button>
              <RightOutlined style={{ fontSize: 10, color: 'var(--ant-color-text-quaternary)' }} />
              <span style={{ fontWeight: 600, fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {projectTitle || '未命名作品'}
              </span>
              <RightOutlined style={{ fontSize: 10, color: 'var(--ant-color-text-quaternary)' }} />
              <span style={{ color: 'var(--ant-color-text-secondary)', fontSize: 14 }}>{PAGE_TITLES[activeKey]}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <ThemeSwitcher />
              <Tooltip title={aiCollapsed ? '展开 AI 助手' : '收起 AI 助手'}>
                <Button
                  type={aiCollapsed ? 'default' : 'primary'}
                  size="small"
                  icon={<RobotOutlined />}
                  onClick={() => setAiCollapsed(!aiCollapsed)}
                >
                  AI 助手
                </Button>
              </Tooltip>
            </div>
          </div>
          {/* Main content */}
          <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
            <Content style={{ padding: 24, background: 'var(--ant-color-bg-container)', flex: 1, minWidth: 0 }}>
              <div key={activeKey} className="moshu-content-enter">
                {renderContent()}
              </div>
            </Content>
            <AiPanelColumn aiCollapsed={aiCollapsed} setAiCollapsed={setAiCollapsed} />
          </div>
        </div>
      </Layout>
    </AiPanelProvider>
  )
}

export default ProjectWorkspace
