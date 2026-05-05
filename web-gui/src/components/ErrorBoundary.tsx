import { Component, type ErrorInfo, type ReactNode } from "react"
import { reportFrontendError } from "../api/errorReporter"

interface Props {
  children: ReactNode
  resetKey?: string
}

interface State {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    reportFrontendError({
      level: "error",
      source: "error-boundary",
      kind: "render_crash",
      message: `${error.name}: ${error.message}`,
      url: window.location.href,
      extra: info.componentStack ?? undefined,
    })
  }

  componentDidUpdate(prevProps: Props) {
    if (this.state.hasError && this.props.resetKey !== prevProps.resetKey) {
      this.setState({ hasError: false, error: null })
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-[400px] items-center justify-center p-8 text-gray-100">
          <div className="max-w-md space-y-4 rounded-xl border border-red-900/60 bg-gray-900/95 p-6 text-center shadow-xl">
            <h2 className="text-xl font-semibold text-red-400">Something went wrong</h2>
            <p className="text-sm text-gray-300">
              {this.state.error?.message ?? "An unexpected error occurred."}
            </p>
            <div className="flex flex-col gap-3 sm:flex-row sm:justify-center">
              <button
                className="rounded bg-gray-100 px-4 py-2 text-sm font-medium text-gray-900 hover:bg-white"
                onClick={() => this.setState({ hasError: false, error: null })}
              >
                Try again
              </button>
              <button
                className="rounded border border-gray-600 px-4 py-2 text-sm font-medium text-gray-100 hover:bg-gray-800"
                onClick={() => {
                  window.location.hash = '#/quality'
                  this.setState({ hasError: false, error: null })
                }}
              >
                Go to Quality
              </button>
            </div>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
