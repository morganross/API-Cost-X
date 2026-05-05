interface ErrorReport {
  level: 'error' | 'warning' | 'info'
  source: string
  message: string
  url?: string
  extra?: string
  kind?: string
  routeClass?: string
  statusClass?: string
}

function truncate(value: string, max = 500): string {
  const trimmed = value.trim()
  return trimmed.length > max ? trimmed.slice(0, max) : trimmed
}

function classifyRouteFromUrl(rawUrl?: string): string {
  try {
    const url = new URL(rawUrl ?? window.location.href, window.location.origin)
    let route = url.hash.startsWith('#') ? url.hash.slice(1) : url.pathname
    route = route.split('?')[0].trim()
    if (!route || route === '/') return 'root'
    return route.replace(/^\/+/, '').split('/')[0]?.toLowerCase() || 'root'
  } catch {
    return 'unknown'
  }
}

export function reportWebGuiError(report: ErrorReport): void {
  const url = report.url ?? window.location.href
  const message = report.extra
    ? `${truncate(report.message, 300)} | ${truncate(report.extra, 500)}`
    : truncate(report.message, 500)

  const payload = {
    level: report.level,
    source: report.source,
    kind: report.kind ?? 'web_gui_event',
    route_class: report.routeClass ?? classifyRouteFromUrl(url),
    status_class: report.statusClass ?? 'none',
    message,
    url,
  }

  if (import.meta.env.DEV) {
    console.debug('[api-cost-x]', payload)
  }
}

export const reportFrontendError = reportWebGuiError
