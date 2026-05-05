import { X, CheckCircle, XCircle, Info, AlertTriangle } from 'lucide-react'
import { useNotifications, type Notification } from '@/stores/notifications'

const icons = {
  success: CheckCircle,
  error: XCircle,
  info: Info,
  warning: AlertTriangle,
}

const colors = {
  success: 'bg-green-600 border-green-500',
  error: 'bg-red-600 border-red-500',
  info: 'bg-blue-600 border-blue-500',
  warning: 'bg-yellow-600 border-yellow-500',
}

function NotificationItem({ notification }: { notification: Notification }) {
  const { remove } = useNotifications()
  const Icon = icons[notification.type]

  return (
    <div
      data-testid={`notification-${notification.type}`}
      data-notification-id={notification.id}
      data-message={notification.message}
      className={`flex items-start gap-3 p-4 rounded-lg border shadow-lg text-white min-w-[300px] max-w-md ${colors[notification.type]}`}
      role="alert"
    >
      <Icon className="w-5 h-5 flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        {notification.title && (
          <p className="font-semibold text-sm" data-testid="notification-title">
            {notification.title}
          </p>
        )}
        <p className="text-sm" data-testid="notification-message">
          {notification.message}
        </p>
      </div>
      <button
        onClick={() => remove(notification.id)}
        className="flex-shrink-0 hover:opacity-80 transition-opacity"
        data-testid="notification-dismiss"
        aria-label="Dismiss notification"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  )
}

export function NotificationContainer() {
  const { notifications } = useNotifications()

  if (notifications.length === 0) return null

  return (
    <div
      data-testid="notification-container"
      data-count={notifications.length}
      className="fixed top-4 right-4 z-50 flex flex-col gap-2"
      aria-live="polite"
    >
      {notifications.map((n) => (
        <NotificationItem key={n.id} notification={n} />
      ))}
    </div>
  )
}
