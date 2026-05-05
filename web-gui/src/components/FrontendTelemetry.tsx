import { useEffect } from 'react'
import { reportWebGuiError } from '../api/errorReporter'

function toErrorMessage(value: unknown): string {
  if (value instanceof Error) return `${value.name}: ${value.message}`
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

export function FrontendTelemetry() {
  useEffect(() => {
    const onWindowError = (event: ErrorEvent) => {
      const message = event.message || toErrorMessage(event.error)
      if (!message) return
      const locationBits = [event.filename, event.lineno, event.colno].filter(Boolean).join(':')
      reportWebGuiError({
        level: 'error',
        source: 'window-error',
        kind: 'window_error',
        message,
        extra: locationBits || undefined,
      })
    }

    const onUnhandledRejection = (event: PromiseRejectionEvent) => {
      const message = toErrorMessage(event.reason)
      if (!message) return
      reportWebGuiError({
        level: 'error',
        source: 'unhandled-rejection',
        kind: 'unhandled_rejection',
        message,
      })
    }

    window.addEventListener('error', onWindowError)
    window.addEventListener('unhandledrejection', onUnhandledRejection)

    return () => {
      window.removeEventListener('error', onWindowError)
      window.removeEventListener('unhandledrejection', onUnhandledRejection)
    }
  }, [])

  return null
}
