import React from 'react'
import ReactDOM from 'react-dom/client'
import { HashRouter, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { FrontendTelemetry } from './components/FrontendTelemetry'
import { applyDesktopShellClass } from './lib/desktopMode'
import './index.css'
import './desktop-mode.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60, // 1 minute
      retry: 1,
    },
  },
})

function RoutedErrorBoundary({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  const resetKey = `${location.pathname}${location.search}${location.hash}`

  return <ErrorBoundary resetKey={resetKey}>{children}</ErrorBoundary>
}

applyDesktopShellClass()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <FrontendTelemetry />
      <HashRouter>
        <RoutedErrorBoundary>
          <App />
        </RoutedErrorBoundary>
      </HashRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)
