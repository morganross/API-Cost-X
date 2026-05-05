import { AlertCircle, RefreshCw } from 'lucide-react'

interface ApiUnavailableGateProps {
  message: string
  onRetry?: () => void
}

export function ApiUnavailableGate({ message, onRetry }: ApiUnavailableGateProps) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <div className="w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-xl">
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            Local API unavailable
          </p>
          <div className="flex items-start gap-3">
            <AlertCircle className="mt-1 h-6 w-6 flex-none text-red-400" />
            <div>
              <h1 className="text-2xl font-semibold text-foreground">Wait for the local API to come back</h1>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">{message}</p>
            </div>
          </div>
        </div>

        <div className="mt-6 space-y-3">
          <button
            type="button"
            onClick={onRetry}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90"
          >
            <RefreshCw className="h-4 w-4" />
            Retry APICostX connection
          </button>
          <p className="text-xs leading-5 text-muted-foreground">
            APICostX will stay paused locally until the local API is reachable again.
          </p>
        </div>
      </div>
    </div>
  )
}
