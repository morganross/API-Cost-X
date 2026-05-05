import { Loader2, CheckCircle, XCircle, Info } from 'lucide-react'

export type StatusType = 'idle' | 'loading' | 'success' | 'error' | 'info'

interface StatusBannerProps {
  status: StatusType
  message?: string
  className?: string
}

const icons = {
  idle: null,
  loading: Loader2,
  success: CheckCircle,
  error: XCircle,
  info: Info,
}

const colors = {
  idle: '',
  loading: 'bg-blue-900/50 border-blue-700 text-blue-200',
  success: 'bg-green-900/50 border-green-700 text-green-200',
  error: 'bg-red-900/50 border-red-700 text-red-200',
  info: 'bg-gray-800 border-gray-700 text-gray-200',
}

export function StatusBanner({ status, message, className = '' }: StatusBannerProps) {
  if (status === 'idle' || !message) return null

  const Icon = icons[status]

  return (
    <div
      data-testid="status-banner"
      data-status={status}
      data-message={message}
      className={`flex items-center gap-3 p-4 rounded-lg border ${colors[status]} ${className}`}
      role={status === 'error' ? 'alert' : 'status'}
    >
      {Icon && (
        <Icon
          className={`w-5 h-5 flex-shrink-0 ${status === 'loading' ? 'animate-spin' : ''}`}
        />
      )}
      <span data-testid="status-message" className="text-sm">
        {message}
      </span>
    </div>
  )
}
