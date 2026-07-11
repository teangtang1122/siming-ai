import { Routes, Route, useLocation, useNavigate } from 'react-router-dom'
import { Alert, Layout, Spin } from 'antd'
import { Suspense, lazy, useEffect, useState } from 'react'
import { useAppStore } from './stores'

const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const ProjectWorkspace = lazy(() => import('./pages/ProjectWorkspace'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const ExternalAgentPage = lazy(() => import('./pages/ExternalAgentPage'))
const GuiPage = lazy(() => import('./pages/GuiPage'))
const ModelCenterPage = lazy(() => import('./pages/ModelCenterPage'))

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
        background: 'var(--ant-color-bg-layout, #f6f2ea)',
      }}
    >
      <div
        style={{
          fontFamily: "'Noto Serif SC', 'LXGW WenKai', serif",
          fontSize: 28,
          fontWeight: 700,
          letterSpacing: '0.1em',
          color: 'var(--ant-color-text, #2c2417)',
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
          color: 'var(--ant-color-text-tertiary, #a89c88)',
          letterSpacing: '0.05em',
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

/** Route guard: redirect to most recent project if exists, otherwise stay on dashboard. */
function RouteGuard() {
  const navigate = useNavigate()
  const location = useLocation()
  const { projects, fetchProjects } = useAppStore()
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    const init = async () => {
      await fetchProjects()
      setChecked(true)
    }
    init()
  }, [fetchProjects])

  useEffect(() => {
    if (!checked) return
    // Only redirect on the root path; /dashboard must remain available for creating more projects.
    if (location.pathname === '/') {
      if (projects.length > 0) {
        const mostRecent = projects[0]
        navigate(`/project/${mostRecent.id}`, { replace: true })
      }
    }
  }, [checked, projects, location.pathname, navigate])

  if (!checked) {
    return <LoadingScreen />
  }

  if (location.pathname === '/' && projects.length > 0) {
    return <LoadingScreen />
  }

  return <DashboardPage />
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
      message={error}
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
      <GlobalErrorBanner />
      <Content style={{ padding: 0 }}>
        <ProjectPreloader />
        <Suspense fallback={<LoadingScreen />}>
          <Routes>
            <Route path="/" element={<RouteGuard />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/project/:projectId/*" element={<ProjectWorkspace />} />
            <Route path="/settings" element={<SettingsPage />} />
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
