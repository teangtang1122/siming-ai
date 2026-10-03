import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './shared/query/client'

const root = ReactDOM.createRoot(document.getElementById('root')!)

async function renderApplication() {
  if (window.location.pathname === '/desktop-pet') {
    const { default: DesktopPetApp } = await import('./features/desktopPet/DesktopPetApp')
    root.render(
      <React.StrictMode>
        <QueryClientProvider client={queryClient}>
          <DesktopPetApp />
        </QueryClientProvider>
      </React.StrictMode>,
    )
    return
  }

  const [{ default: App }, { AppProviders }] = await Promise.all([
    import('./App'),
    import('./app/providers/AppProviders'),
    import('./app/mainAppStyles'),
  ])
  root.render(
    <React.StrictMode>
      <AppProviders>
        <App />
      </AppProviders>
    </React.StrictMode>,
  )
}

void renderApplication()
