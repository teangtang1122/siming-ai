import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Layout, Menu, Spin, Tooltip } from 'antd'
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { useAppStore } from '../stores'
import {
  AuditOutlined,
  BarChartOutlined,
  BookOutlined,
  BranchesOutlined,
  ExportOutlined,
  FileAddOutlined,
  FileTextOutlined,
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
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import AiSidePanel from '../components/AiSidePanel'
import TabCache from '../components/TabCache'
import WorkspaceAssistantChat from '../components/WorkspaceAssistantChat'
import { AiPanelProvider, useAiPanelContext } from '../contexts/AiPanelContext'
import { useModelOptions } from '../hooks/useModelOptions'
import { usePanelResize } from '../hooks/usePanelResize'
import ThemeSwitcher from '../themes/ThemeSwitcher'
import './ProjectWorkspace.css'

const { Sider, Content } = Layout

const WorldbuildingPage = lazy(() => import('./WorldbuildingPage'))
const CharactersPage = lazy(() => import('./CharactersPage'))
const OutlinePage = lazy(() => import('./OutlinePage'))
const WriterPage = lazy(() => import('./WriterPage'))
const StatsPage = lazy(() => import('./StatsPage'))
const ExportPage = lazy(() => import('./ExportPage'))
const DeconstructPage = lazy(() => import('./DeconstructPage'))
const VisualizationPage = lazy(() => import('./VisualizationPage'))
const ImportPage = lazy(() => import('./ImportPage'))
const CatalogingPage = lazy(() => import('./CatalogingPage'))
const SkillsPage = lazy(() => import('./SkillsPage'))
const PromptPacksPage = lazy(() => import('./PromptPacksPage'))
const ScheduledTasksPage = lazy(() => import('./ScheduledTasksPage').then((module) => ({ default: module.ScheduledTasksPage })))
const NarrativeGovernancePage = lazy(() => import('./NarrativeGovernancePage'))
const ContextGovernancePage = lazy(() => import('./ContextGovernancePage'))

type MenuKey = 'world' | 'characters' | 'outline' | 'writer' | 'export' | 'stats' | 'deconstruct' | 'cataloging' | 'visualization' | 'governance' | 'context' | 'import' | 'skills' | 'prompts' | 'scheduler'

/** Menu key → Chinese page title mapping */
const PAGE_TITLES: Record<MenuKey, string> = {
  context: '上下文治理',
  writer: '写作工作台',
  outline: '大纲规划',
  characters: '角色管理',
  world: '世界观',
  stats: '统计追踪',
  deconstruct: '拆书分析',
  cataloging: '作品建档',
  visualization: '可视化',
  governance: '叙事治理',
  skills: '技能管理',
  prompts: '提示词投稿',
  scheduler: '自动任务',
  import: '内容导入',
  export: '导出',
}

function AiPanelColumn({ aiCollapsed, setAiCollapsed }: { aiCollapsed: boolean; setAiCollapsed: (v: boolean) => void }) {
  const { projectId } = useParams<{ projectId: string }>()
  const { defaultModel } = useModelOptions()
  const { width: aiWidth, onDragHandleMouseDown: onAiResize, dragging: aiDragging } = usePanelResize({
    initialWidth: Math.min(560, Math.max(280, window.innerWidth * 0.24)),
  })
  const { selectedOutlineNodeId, selectedCharacterId, selectedText, selectedTextChapterId, triggerRefresh } = useAiPanelContext()
  // Keep chat mounted once opened to avoid re-fetching on toggle
  const [hasBeenOpened, setHasBeenOpened] = useState(!aiCollapsed)

  useEffect(() => {
    if (!aiCollapsed) setHasBeenOpened(true)
  }, [aiCollapsed])

  return (
    <AiSidePanel
      collapsed={aiCollapsed}
      onToggle={() => setAiCollapsed(true)}
      width={aiWidth}
      onResizeHandle={onAiResize}
      dragging={aiDragging}
    >
      {hasBeenOpened && (
        <WorkspaceAssistantChat
          projectId={projectId!}
          scope="project"
          selectedOutlineNodeId={selectedOutlineNodeId}
          selectedCharacterId={selectedCharacterId}
          selectedText={selectedText}
          selectedTextChapterId={selectedTextChapterId}
          defaultModel={defaultModel}
          onApplied={triggerRefresh}
        />
      )}
    </AiSidePanel>
  )
}

function usePersistedState(key: string, defaultValue: boolean): [boolean, (v: boolean | ((prev: boolean) => boolean)) => void] {
  const [state, setState] = useState<boolean>(() => {
    try {
      const stored = localStorage.getItem(`siming_${key}`) ?? localStorage.getItem(`moshu_${key}`)
      return stored !== null ? stored === 'true' : defaultValue
    } catch { return defaultValue }
  })
  const setPersisted = useCallback((v: boolean | ((prev: boolean) => boolean)) => {
    setState(prev => {
      const next = typeof v === 'function' ? v(prev) : v
      try {
        localStorage.setItem(`siming_${key}`, String(next))
        localStorage.removeItem(`moshu_${key}`)
      } catch {}
      return next
    })
  }, [key])
  return [state, setPersisted]
}

function ProjectWorkspace() {
  const { projectId } = useParams<{ projectId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const [sidebarCollapsed, setSidebarCollapsed] = usePersistedState('sidebar_collapsed', false)
  const [aiCollapsed, setAiCollapsed] = usePersistedState('ai_panel_collapsed', true)
  const navigate = useNavigate()
  const { projects, getProject } = useAppStore()
  const requestedView = searchParams.get('view') as MenuKey | null
  const activeKey: MenuKey = requestedView && requestedView in PAGE_TITLES ? requestedView : 'writer'

  const selectView = useCallback((view: MenuKey) => {
    const next = new URLSearchParams(searchParams)
    if (view === 'writer') next.delete('view')
    else next.set('view', view)
    setSearchParams(next, { replace: false })
  }, [searchParams, setSearchParams])

  useEffect(() => {
    if (window.innerWidth < 900) setSidebarCollapsed(true)
  }, [setSidebarCollapsed])

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
    { key: 'writer', icon: <BookOutlined />, label: '写作' },
    { key: 'outline', icon: <BranchesOutlined />, label: '大纲' },
    {
      key: 'story-library',
      icon: <TeamOutlined />,
      label: '故事资料',
      children: [
        { key: 'characters', icon: <TeamOutlined />, label: '角色' },
        { key: 'world', icon: <GlobalOutlined />, label: '世界观' },
        { key: 'visualization', icon: <ApartmentOutlined />, label: '关系图' },
      ],
    },
    {
      key: 'continuity',
      icon: <SafetyCertificateOutlined />,
      label: '连续性与档案',
      children: [
        { key: 'governance', icon: <SafetyCertificateOutlined />, label: '叙事治理' },
        { key: 'context', icon: <AuditOutlined />, label: '上下文治理' },
        { key: 'cataloging', icon: <DatabaseOutlined />, label: '作品建档' },
        { key: 'stats', icon: <BarChartOutlined />, label: '统计追踪' },
      ],
    },
    {
      key: 'toolbox',
      icon: <ThunderboltOutlined />,
      label: '工具与发布',
      children: [
        { key: 'deconstruct', icon: <ThunderboltOutlined />, label: '拆书分析' },
        { key: 'import', icon: <FileAddOutlined />, label: '内容导入' },
        { key: 'export', icon: <ExportOutlined />, label: '导出作品' },
        { key: 'scheduler', icon: <ClockCircleOutlined />, label: '自动任务' },
        { key: 'skills', icon: <BulbOutlined />, label: '技能管理' },
        { key: 'prompts', icon: <FileTextOutlined />, label: '提示词投稿' },
      ],
    },
  ]

  const menuParentByView: Partial<Record<MenuKey, string>> = {
    characters: 'story-library',
    world: 'story-library',
    visualization: 'story-library',
    governance: 'continuity',
    context: 'continuity',
    cataloging: 'continuity',
    stats: 'continuity',
    deconstruct: 'toolbox',
    import: 'toolbox',
    export: 'toolbox',
    scheduler: 'toolbox',
    skills: 'toolbox',
    prompts: 'toolbox',
  }

  /** Tab renderers — wrapped in closures for TabCache lazy evaluation */
  const tabRenderers = projectId
    ? {
        context: () => <ContextGovernancePage projectId={projectId} />,
        writer: () => <WriterPage projectId={projectId} />,
        outline: () => <OutlinePage projectId={projectId} />,
        characters: () => <CharactersPage projectId={projectId} />,
        world: () => <WorldbuildingPage projectId={projectId} />,
        stats: () => <StatsPage projectId={projectId} />,
        deconstruct: () => <DeconstructPage projectId={projectId} />,
        cataloging: () => <CatalogingPage projectId={projectId} />,
        visualization: () => <VisualizationPage projectId={projectId} />,
        governance: () => <NarrativeGovernancePage projectId={projectId} />,
        skills: () => <SkillsPage projectId={projectId} />,
        prompts: () => <PromptPacksPage projectId={projectId} />,
        scheduler: () => <ScheduledTasksPage projectId={projectId} />,
        import: () => <ImportPage projectId={projectId} />,
        export: () => <ExportPage projectId={projectId} />,
      }
    : null

  return (
    <AiPanelProvider>
      <Layout className="project-workspace">
        <Sider
          width={208}
          collapsedWidth={58}
          collapsible
          collapsed={sidebarCollapsed}
          onCollapse={setSidebarCollapsed}
          trigger={null}
          theme="light"
          className="project-workspace-sider"
        >
          <div className="project-workspace-brand">
            {!sidebarCollapsed && (
              <span title={projectTitle}>
                {projectTitle || (projectId ? projectId.slice(0, 8) + '...' : '')}
              </span>
            )}
            <Button
              type="text"
              icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setSidebarCollapsed((value) => !value)}
              aria-label={sidebarCollapsed ? '展开项目导航' : '收起项目导航'}
            />
          </div>
          <Menu
            mode="inline"
            inlineCollapsed={sidebarCollapsed}
            selectedKeys={[activeKey]}
            defaultOpenKeys={menuParentByView[activeKey] ? [menuParentByView[activeKey]!] : []}
            onClick={({ key }) => {
              if (key in PAGE_TITLES) selectView(key as MenuKey)
            }}
            items={menuItems}
            className="project-workspace-menu"
          />
        </Sider>
        <div className="project-workspace-column">
          <header className="project-workspace-header">
            <div className="project-workspace-breadcrumb">
              <Button
                type="link"
                icon={<HomeOutlined />}
                onClick={() => navigate('/dashboard')}
              >
                作品库
              </Button>
              <span aria-hidden="true">/</span>
              <Button
                type="link"
                onClick={() => selectView('writer')}
                className="project-workspace-title"
              >
                {projectTitle || '未命名作品'}
              </Button>
              <span aria-hidden="true">/</span>
              <span>{PAGE_TITLES[activeKey]}</span>
            </div>
            <div className="project-workspace-actions">
              <ThemeSwitcher />
              <Tooltip title={aiCollapsed ? '展开 AI 助手' : '收起 AI 助手'}>
                <Button
                  type={aiCollapsed ? 'default' : 'primary'}
                  size="small"
                  icon={<RobotOutlined />}
                  onClick={() => setAiCollapsed(!aiCollapsed)}
                  aria-expanded={!aiCollapsed}
                >
                  项目助手
                </Button>
              </Tooltip>
            </div>
          </header>
          <div className="project-workspace-main">
            <Content className="project-workspace-content">
              {tabRenderers && (
                <Suspense fallback={<div className="project-workspace-loading" role="status"><Spin /><span>正在打开{PAGE_TITLES[activeKey]}...</span></div>}>
                  <TabCache activeKey={activeKey} tabs={tabRenderers} />
                </Suspense>
              )}
            </Content>
            <AiPanelColumn aiCollapsed={aiCollapsed} setAiCollapsed={setAiCollapsed} />
          </div>
        </div>
      </Layout>
    </AiPanelProvider>
  )
}

export default ProjectWorkspace
