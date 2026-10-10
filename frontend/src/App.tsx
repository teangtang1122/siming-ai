import { ContextInspectorHost } from './features/contextInspector/ContextInspector'
import { Routes, Route, useLocation, useNavigate } from 'react-router-dom'
import { Alert, Layout, Spin } from 'antd'
import { Suspense, lazy, useEffect } from 'react'
import { useAppStore } from './stores'
import GlobalOperationCenter from './features/operations/components/GlobalOperationCenter'
import { useGettingStartedStatus } from './features/onboarding'
import GatewayAdminGate, { useGatewayRuntime } from './features/gateway/GatewayAdminGate'

const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const ProjectWorkspace = lazy(() => import('./pages/ProjectWorkspace'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const AboutPage = lazy(() => import('./pages/AboutPage'))
const GettingStartedPage = lazy(() => import('./pages/GettingStartedPage'))
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

/** Send a brand-new, unconfigured author to the model setup once. */
function FirstRunSetupGate() {
  const location = useLocation()
  const navigate = useNavigate()
  const { headless } = useGatewayRuntime()
  const onLibraryRoute = ['/', '/dashboard'].includes(location.pathname)
  const { data } = useGettingStartedStatus(onLibraryRoute && !headless)

  useEffect(() => {
    if (!onLibraryRoute) return
    if (headless) {
      navigate('/settings', { replace: true })
      return
    }
    if (localStorage.getItem('siming_getting_started_deferred') === 'true') return
    if (data?.needs_setup) navigate('/getting-started', { replace: true })
  }, [data?.needs_setup, headless, navigate, onLibraryRoute])

  return null
}

function WildcardRedirect() {
  const navigate = useNavigate()

  useEffect(() => {
    navigate('/', { replace: true })
  }, [navigate])

  return null
}

/** The creation workbench is now an inline editor in the AI assistant. */
function NovelCreationRedirect() {
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    const source = new URLSearchParams(location.search)
    const target = new URLSearchParams()
    const session = source.get('session') || source.get('creationSession')
    const conversation = source.get('conversation')
    const artifact = source.get('stage') || source.get('artifact')
    const importId = source.get('import')
    if (session) target.set('creationSession', session)
    if (conversation) target.set('conversation', conversation)
    if (artifact) target.set('artifact', artifact)
    if (importId) target.set('import', importId)
    navigate(`/gui${target.size ? `?${target.toString()}` : ''}`, { replace: true })
  }, [location.search, navigate])

  return <LoadingScreen />
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
      <GatewayAdminGate>
        <a className="siming-skip-link" href="#main-content">跳到主要内容</a>
        <GlobalErrorBanner />
        <GlobalOperationCenter />
        <ContextInspectorHost />
        <Content id="main-content" tabIndex={-1} style={{ padding: 0 }}>
          <FirstRunSetupGate />
          <Suspense fallback={<LoadingScreen />}>
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/novel-creation" element={<NovelCreationRedirect />} />
              <Route path="/project/:projectId/*" element={<ProjectWorkspace />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/about" element={<AboutPage />} />
              <Route path="/getting-started" element={<GettingStartedPage />} />
              <Route path="/external-agent" element={<ExternalAgentPage />} />
              <Route path="/gui" element={<GuiPage />} />
              <Route path="/models" element={<ModelCenterPage />} />
              <Route path="*" element={<WildcardRedirect />} />
            </Routes>
          </Suspense>
        </Content>
      </GatewayAdminGate>
    </Layout>
  )
}

export default App
