import { Outlet, Link, useLocation } from 'react-router-dom'
import {
  FileText,
  Settings,
  Menu,
  X,
  Sliders,
  Library,
  BarChart2,
  Play,
  Loader2,
} from 'lucide-react'
import { useState, useEffect, type CSSProperties } from 'react'
import { useRef } from 'react'
import { cn } from '@/lib/utils'
import { experimentNavigation } from '@/experiments/registry'
import { getApiBootstrapStatus } from '@/api/client'
import { ApiUnavailableGate } from '@/components/ApiUnavailableGate'


export default function Layout() {
  const location = useLocation()
  const isContentWorkspace = location.pathname.startsWith('/content')
  const hasAcmUser = true
  const menuButtonRef = useRef<HTMLButtonElement | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [isCompactNav, setIsCompactNav] = useState(false)
  const [apiUnavailableMessage, setApiUnavailableMessage] = useState<string | null>(null)
  const [readinessCheckPending, setReadinessCheckPending] = useState(
    () => false
  )
  const [readinessRetryTick, setReadinessRetryTick] = useState(0)


  useEffect(() => {
    if (!hasAcmUser) {
      setReadinessCheckPending(false)
      return
    }


    let cancelled = false
    setReadinessCheckPending(true)
    setApiUnavailableMessage(null)

    getApiBootstrapStatus()
      .then((status) => {
        if (cancelled) {
          return
        }

        if (status.ready) {
          setApiUnavailableMessage(null)
          return
        }

        if (status.apiUnavailable) {
          setApiUnavailableMessage(
            status.message || 'The API service is temporarily unavailable. Please try again in a moment.'
          )
          return
        }
      })
      .catch(() => {
        if (!cancelled) {
          setApiUnavailableMessage('The API service is temporarily unavailable. Please try again in a moment.')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setReadinessCheckPending(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [hasAcmUser, readinessRetryTick])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    const mediaQuery = window.matchMedia('(max-width: 1023px)')
    const syncCompactNav = () => setIsCompactNav(mediaQuery.matches)

    syncCompactNav()
    mediaQuery.addEventListener('change', syncCompactNav)

    return () => mediaQuery.removeEventListener('change', syncCompactNav)
  }, [])


  useEffect(() => {
    if (!isCompactNav && sidebarOpen) {
      setSidebarOpen(false)
    }
  }, [isCompactNav, sidebarOpen])

  useEffect(() => {
    if (!isCompactNav || !sidebarOpen) {
      return
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return
      }

      event.preventDefault()
      setSidebarOpen(false)
      window.requestAnimationFrame(() => {
        menuButtonRef.current?.focus()
      })
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [isCompactNav, sidebarOpen])

  const navigation = [
    { name: 'Presets', href: '/presets', icon: Sliders },
    ...experimentNavigation,
    { name: 'Execute', href: '/execute', icon: Play },
    { name: 'Content Library', href: '/content', icon: Library },
    { name: 'History', href: '/history', icon: FileText },
    { name: 'Quality', href: '/quality', icon: BarChart2 },
    { name: 'Settings', href: '/settings', icon: Settings },
  ]
  const rootStyle = {
    '--apicostx-mobile-app-bar-height': isCompactNav ? '3.5rem' : '0px',
    '--apicostx-page-sticky-top': 'var(--apicostx-mobile-app-bar-height, 0px)',
    ...(isContentWorkspace ? { height: '100dvh' } : {}),
  } as CSSProperties

  return (
    <div
      className={cn('bg-background', isContentWorkspace ? 'overflow-hidden' : 'min-h-screen')}
      style={rootStyle}
    >
      {/* Mobile sidebar backdrop */}
      {isCompactNav && sidebarOpen && (
        <div
          className="fixed inset-x-0 bottom-0 z-40 bg-black/50"
          style={{ top: 'var(--apicostx-page-sticky-top, var(--apicostx-header-offset, 173px))' }}
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        id="apicostx-app-sidebar"
        className={cn(
          'z-[60] flex w-64 flex-col overflow-y-auto overflow-x-hidden bg-card border-r transform transition-transform duration-200 ease-in-out',
          isCompactNav ? (sidebarOpen ? 'translate-x-0' : '-translate-x-full') : 'translate-x-0'
        )}
        style={{
          position: 'fixed',
          top: 'var(--apicostx-page-sticky-top, var(--apicostx-header-offset, 173px))',
          bottom: 0,
          left: 0,
        }}
      >
        <button
          className={cn('absolute right-2 top-2 z-10 rounded-md p-1.5 hover:bg-accent', !isCompactNav && 'hidden')}
          onClick={() => setSidebarOpen(false)}
        >
          <X className="h-5 w-5" />
        </button>
        <div className="flex min-h-full flex-col">
          <div className="flex-1 p-3 pt-3">
            <nav className="space-y-1">
              {navigation.map((item) => {
                const isActive = item.href
                  ? item.href === '/'
                    ? location.pathname === '/'
                    : item.href === '/execute'
                      ? location.pathname === '/execute' || location.pathname.startsWith('/execute/')
                      : item.href.startsWith('/execute/')
                        ? location.pathname.startsWith('/execute/')
                        : item.href.startsWith('/presets')
                          ? location.pathname === item.href
                          : location.pathname === item.href || location.pathname.startsWith(`${item.href}/`)
                  : false
                if (item.href === null) {
                  return (
                    <span
                      key={item.name}
                      className="flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium text-muted-foreground/40 cursor-not-allowed"
                      title="No runs yet"
                    >
                      <item.icon className="h-5 w-5" />
                      {item.name}
                    </span>
                  )
                }
                return (
                  <Link
                    key={item.name}
                    to={item.href}
                    className={cn(
                      'flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors',
                      isActive
                        ? 'bg-primary text-primary-foreground'
                        : 'text-muted-foreground hover:bg-accent hover:text-foreground'
                    )}
                    onClick={() => setSidebarOpen(false)}
                  >
                    <item.icon className="h-5 w-5" />
                    {item.name}
                  </Link>
                )
              })}
            </nav>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <div className={cn('flex min-h-full flex-col', !isCompactNav && 'pl-64', isContentWorkspace && 'h-full overflow-hidden')}>
        {isCompactNav && (
          <div
            className="sticky z-30 border-b border-border bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/85"
            style={{ top: 0 }}
          >
            <div className="flex h-14 items-center gap-3 px-4">
              <button
                type="button"
                aria-label="Open APICostX menu"
                aria-controls="apicostx-app-sidebar"
                aria-expanded={sidebarOpen}
                ref={menuButtonRef}
                className="flex h-10 w-10 flex-none items-center justify-center rounded-md border border-border bg-card text-foreground shadow-sm transition hover:bg-accent"
                onClick={() => setSidebarOpen((open) => !open)}
              >
                <Menu className="h-5 w-5" />
              </button>
              <div className="min-w-0 flex-1">
                <span className="block truncate text-sm font-semibold uppercase tracking-[0.18em] text-foreground">
                  API service
                </span>
              </div>
            </div>
          </div>
        )}

        {/* Page content */}
        <main className={cn('flex-1 px-4 pb-4 pt-4 lg:px-6 lg:pb-6 lg:pt-6', isContentWorkspace && 'min-h-0 overflow-hidden')}>
          {apiUnavailableMessage ? (
            <ApiUnavailableGate
              message={apiUnavailableMessage}
              onRetry={() => setReadinessRetryTick((value) => value + 1)}
            />
          ) : readinessCheckPending ? (
            <div className="flex min-h-screen items-center justify-center px-4 py-10">
              <div className="w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-xl">
                <div className="flex items-center gap-3 text-foreground">
                  <Loader2 className="h-5 w-5 animate-spin text-primary" />
                  <div>
                    <p className="text-sm font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                      APICostX startup check
                    </p>
                    <p className="mt-1 text-sm text-muted-foreground">
                      Checking the local API before loading the workspace.
                    </p>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <Outlet />
          )}
        </main>
      </div>
    </div>
  )
}
