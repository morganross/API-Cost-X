import { cn } from '@/lib/utils'

type RunStatus =
  | 'pending'
  | 'running'
  | 'paused'
  | 'completed'
  | 'completed_with_errors'
  | 'failed'
  | 'cancelled'

interface RunStatusBadgeProps {
  status: RunStatus
  pauseRequested?: number
  className?: string
}

const statusConfig: Record<RunStatus, { label: string; className: string }> = {
  pending: {
    label: 'Pending',
    className: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200',
  },
  running: {
    label: 'Running',
    className: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200',
  },
  paused: {
    label: 'Paused',
    className: 'bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200',
  },
  completed: {
    label: 'Completed',
    className: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200',
  },
  completed_with_errors: {
    label: 'Completed w/ Errors',
    className: 'bg-amber-100 text-amber-900 dark:bg-amber-900 dark:text-amber-200',
  },
  failed: {
    label: 'Failed',
    className: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200',
  },
  cancelled: {
    label: 'Cancelled',
    className: 'bg-gray-100 text-gray-800 dark:bg-gray-900 dark:text-gray-200',
  },
}

export default function RunStatusBadge({ status, pauseRequested, className }: RunStatusBadgeProps) {
  const config = status === 'running' && pauseRequested === 1
    ? { label: 'Pausing…', className: 'bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200' }
    : statusConfig[status] ?? statusConfig['failed']

  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium',
        config.className,
        className
      )}
    >
      {config.label}
    </span>
  )
}
