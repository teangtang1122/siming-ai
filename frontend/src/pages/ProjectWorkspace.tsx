import { useParams, useNavigate } from 'react-router-dom'
import { Button, Layout, Menu, Tooltip } from 'antd'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useAppStore } from '../stores'
import {
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
import PromptPacksPage from './PromptPacksPage'
import { ScheduledTasksPage } from './ScheduledTasksPage'
import NarrativeGovernancePage from './NarrativeGovernancePage'
import AiSidePanel from '../components/AiSidePanel'
import TabCache from '../components/TabCache'
import WorkspaceAssistantChat from '../components/WorkspaceAssistantChat'
import { AiPanelProvider, useAiPanelContext } from '../contexts/AiPanelContext'
import { useModelOptions } from '../hooks/useModelOptions'
import { usePanelResize } from '../hooks/usePanelResize'
import ThemeSwitcher from '../themes/ThemeSwitcher'

const { Sider, Content } = Layout

type MenuKey = 'world' | 'characters' | 'outline' | 'writer' | 'export' | 'stats' | 'deconstruct' | 'cataloging' | 'visualization' | 'governance' | 'import' | 'skills' | 'prompts' | 'scheduler'

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
  const [activeKey, setActiveKey] = useState<MenuKey>('writer')
  const [sidebarCollapsed, setSidebarCollapsed] = usePersistedState('sidebar_collapsed', false)
  const [aiCollapsed, setAiCollapsed] = usePersistedState('ai_panel_collapsed', true)
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
    ...(sidebarCollapsed ? [{ type: 'divider' as const }] : []),
    {
      type: 'group' as const,
      label: sidebarCollapsed ? '' : '工具',
      children: [
        { key: 'stats', icon: <BarChartOutlined />, label: '统计追踪' },
        { key: 'deconstruct', icon: <ThunderboltOutlined />, label: '拆书分析' },
        { key: 'cataloging', icon: <DatabaseOutlined />, label: '作品建档' },
        { key: 'visualization', icon: <ApartmentOutlined />, label: '可视化' },
        { key: 'governance', icon: <SafetyCertificateOutlined />, label: '叙事治理' },
      ],
    },
    ...(sidebarCollapsed ? [{ type: 'divider' as const }] : []),
    {
      type: 'group' as const,
      label: sidebarCollapsed ? '' : '设置',
      children: [
        { key: 'skills', icon: <BulbOutlined />, label: '技能管理' },
        { key: 'prompts', icon: <FileTextOutlined />, label: '提示词投稿' },
        { key: 'scheduler', icon: <ClockCircleOutlined />, label: '自动任务' },
        { key: 'import', icon: <FileAddOutlined />, label: '内容导入' },
        { key: 'export', icon: <ExportOutlined />, label: '导出' },
      ],
    },
  ]

  /** Tab renderers — wrapped in closures for TabCache lazy evaluation */
  const tabRenderers = projectId
    ? {
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
      <Layout style={{ minHeight: '100vh' }}>
        <Sider
          width={188}
          collapsedWidth={52}
          collapsible
          collapsed={sidebarCollapsed}
          onCollapse={setSidebarCollapsed}
          trigger={null}
          theme="light"
          style={{
            borderRight: '1px solid var(--ant-color-border-secondary)',
            transition: 'width 0.25s cubic-bezier(0.22, 1, 0.36, 1)',
          }}
        >
          {/* Sidebar header — project title area */}
          <div
            style={{
              alignItems: 'center',
              borderBottom: '1px solid var(--ant-color-border-secondary)',
              display: 'flex',
              fontWeight: 'bold',
              gap: 8,
              justifyContent: sidebarCollapsed ? 'center' : 'space-between',
              minHeight: 56,
              padding: sidebarCollapsed ? '8px 0' : '12px 16px',
              background: 'linear-gradient(180deg, rgba(0,0,0,0.015) 0%, transparent 100%)',
            }}
          >
            {!sidebarCollapsed && (
              <span
                style={{
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  fontSize: 14,
                  fontFamily: "'Noto Serif SC', 'LXGW WenKai', serif",
                  letterSpacing: '0.02em',
                }}
              >
                {projectTitle || (projectId ? projectId.slice(0, 8) + '...' : '')}
              </span>
            )}
            <Button
              type="text"
              icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setSidebarCollapsed((value) => !value)}
              style={{ opacity: 0.75 }}
            />
          </div>
          <Menu
            mode="inline"
            inlineCollapsed={sidebarCollapsed}
            selectedKeys={[activeKey]}
            onClick={({ key }) => setActiveKey(key as MenuKey)}
            items={menuItems}
            style={{ borderRight: 0, paddingTop: 8 }}
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
              background: 'var(--ant-color-bg-container)',
              flexShrink: 0,
              backdropFilter: 'blur(8px)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
              <Button
                type="link"
                icon={<HomeOutlined />}
                onClick={() => navigate('/dashboard')}
                style={{ padding: '0 4px', fontSize: 13, opacity: 0.7 }}
              >
                作品管理
              </Button>
              <span style={{ fontSize: 10, color: 'var(--ant-color-text-quaternary)', margin: '0 2px' }}>›</span>
              <Button
                type="link"
                onClick={() => setActiveKey('writer')}
                style={{
                  padding: '0 4px',
                  fontWeight: 600,
                  fontSize: 13,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  fontFamily: "'Noto Serif SC', 'LXGW WenKai', serif",
                  letterSpacing: '0.01em',
                  maxWidth: 200,
                }}
              >
                {projectTitle || '未命名作品'}
              </Button>
              <span style={{ fontSize: 10, color: 'var(--ant-color-text-quaternary)', margin: '0 2px' }}>›</span>
              <span style={{ color: 'var(--ant-color-text-secondary)', fontSize: 13 }}>{PAGE_TITLES[activeKey]}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <ThemeSwitcher />
              <Tooltip title={aiCollapsed ? '展开 AI 助手' : '收起 AI 助手'}>
                <Button
                  type={aiCollapsed ? 'default' : 'primary'}
                  size="small"
                  icon={<RobotOutlined />}
                  onClick={() => setAiCollapsed(!aiCollapsed)}
                  style={{
                    borderRadius: 6,
                    transition: 'all 0.2s ease',
                  }}
                >
                  AI 助手
                </Button>
              </Tooltip>
            </div>
          </div>
          {/* Main content */}
          <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
            <Content style={{ padding: 24, background: 'var(--ant-color-bg-container)', flex: 1, minWidth: 0 }}>
              {tabRenderers && (
                <TabCache activeKey={activeKey} tabs={tabRenderers} />
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
