import { Routes, Route, useLocation, useNavigate } from 'react-router-dom'
import { Layout, Spin } from 'antd'
import { Suspense, lazy, useEffect, useState } from 'react'
import { useAppStore } from './stores'

const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const ProjectWorkspace = lazy(() => import('./pages/ProjectWorkspace'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const ExternalAgentPage = lazy(() => import('./pages/ExternalAgentPage'))
const GuiPage = lazy(() => import('./pages/GuiPage'))

const { Content } = Layout

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
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spin size="large" tip="加载中..." />
      </div>
    )
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

function App() {
  return (
    <Layout style={{ minHeight: '100vh' }} className="moshu-grain">
      <Content style={{ padding: 0 }}>
        <ProjectPreloader />
        <Suspense
          fallback={
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
              <Spin size="large" tip="加载中..." />
            </div>
          }
        >
          <Routes>
            <Route path="/" element={<RouteGuard />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/project/:projectId/*" element={<ProjectWorkspace />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/external-agent" element={<ExternalAgentPage />} />
            <Route path="/gui" element={<GuiPage />} />
            <Route path="*" element={<WildcardRedirect />} />
          </Routes>
        </Suspense>
      </Content>
    </Layout>
  )
}

export default App
