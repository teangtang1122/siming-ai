import { Routes, Route, useLocation, useNavigate } from 'react-router-dom'
import { Alert, Layout, Spin } from 'antd'
import { Suspense, lazy, useEffect } from 'react'
import { useAppStore } from './stores'
import { apiClient } from './api/client'
import GlobalOperationCenter from './components/GlobalOperationCenter'

const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const ProjectWorkspace = lazy(() => import('./pages/ProjectWorkspace'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const GettingStartedPage = lazy(() => import('./pages/GettingStartedPage'))
const ExternalAgentPage = lazy(() => import('./pages/ExternalAgentPage'))
const GuiPage = lazy(() => import('./pages/GuiPage'))
const ModelCenterPage = lazy(() => import('./pages/ModelCenterPage'))
const NovelCreationWizardPage = lazy(() => import('./pages/NovelCreationWizardPage'))

const { Content } = Layout

/** Branded loading spinner */
function LoadingScreen() {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        gap: 16,
        background: 'var(--ant-color-bg-layout, #f4f4f1)',
      }}
    >
      <div
        style={{
          fontFamily: "'Noto Serif SC', 'LXGW WenKai', serif",
          fontSize: 28,
          fontWeight: 700,
          letterSpacing: 0,
          color: 'var(--ant-color-text, #20201f)',
          opacity: 0.8,
          marginBottom: 4,
        }}
      >
        司命
      </div>
      <Spin size="default" />
      <div
        style={{
          fontSize: 13,
          color: 'var(--ant-color-text-tertiary, #8a8883)',
          letterSpacing: 0,
        }}
      >
        正在加载...
      </div>
    </div>
  )
}

/** Keep project metadata fresh on non-dashboard routes. */
function ProjectPreloader() {
  const location = useLocation()
  const { fetchProjects } = useAppStore()

  useEffect(() => {
    if (location.pathname !== '/' && location.pathname !== '/dashboard') {
      fetchProjects()
    }
  }, [fetchProjects, location.pathname])

  return null
}

/** Send a brand-new, unconfigured author to the zero-command setup once. */
function FirstRunSetupGate() {
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    if (!['/', '/dashboard'].includes(location.pathname)) return
    if (localStorage.getItem('siming_getting_started_deferred') === 'true') return
    let cancelled = false
    const request = apiClient.get<{ data: { needs_setup: boolean } }>('/config/getting-started', { summary: true })
    if (!request || typeof request.then !== 'function') return
    request
      .then((response) => {
        if (!cancelled && response.data.data.needs_setup) navigate('/getting-started', { replace: true })
      })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [location.pathname, navigate])

  return null
}

function WildcardRedirect() {
  const navigate = useNavigate()

  useEffect(() => {
    navigate('/', { replace: true })
  }, [navigate])

  return null
}

/** Global error banner — renders store errors as a dismissible alert. */
function GlobalErrorBanner() {
  const error = useAppStore((s) => s.error)
  const setError = useAppStore((s) => s.setError)

  if (!error) return null

  return (
    <Alert
      type="error"
      message="操作未完成"
      description={error}
      closable
      onClose={() => setError(null)}
      banner
      style={{ position: 'sticky', top: 0, zIndex: 1100 }}
    />
  )
}

function App() {
  return (
    <Layout style={{ minHeight: '100vh' }} className="siming-grain">
      <a className="siming-skip-link" href="#main-content">跳到主要内容</a>
      <GlobalErrorBanner />
      <GlobalOperationCenter />
      <Content id="main-content" tabIndex={-1} style={{ padding: 0 }}>
        <ProjectPreloader />
        <FirstRunSetupGate />
        <Suspense fallback={<LoadingScreen />}>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/novel-creation" element={<NovelCreationWizardPage />} />
            <Route path="/project/:projectId/*" element={<ProjectWorkspace />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/getting-started" element={<GettingStartedPage />} />
            <Route path="/external-agent" element={<ExternalAgentPage />} />
            <Route path="/gui" element={<GuiPage />} />
            <Route path="/models" element={<ModelCenterPage />} />
            <Route path="*" element={<WildcardRedirect />} />
          </Routes>
        </Suspense>
      </Content>
    </Layout>
  )
}

export default App
