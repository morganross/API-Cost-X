import { create } from 'zustand'

export interface Notification {
  id: string
  type: 'success' | 'error' | 'info' | 'warning'
  message: string
  title?: string
  duration?: number  // ms, 0 = persist until dismissed
}

interface NotificationStore {
  notifications: Notification[]
  add: (type: Notification['type'], message: string, options?: { title?: string; duration?: number }) => string
  remove: (id: string) => void
  clear: () => void
}

export const useNotifications = create<NotificationStore>((set) => ({
  notifications: [],

  add: (type, message, options = {}) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
    const duration = options.duration ?? 5000  // Default 5 seconds

    set((state) => ({
      notifications: [...state.notifications, {
        id,
        type,
        message,
        title: options.title,
        duration
      }]
    }))

    // Auto-remove after duration (unless duration is 0)
    if (duration > 0) {
      setTimeout(() => {
        set((state) => ({
          notifications: state.notifications.filter(n => n.id !== id)
        }))
      }, duration)
    }

    return id
  },

  remove: (id) => set((state) => ({
    notifications: state.notifications.filter(n => n.id !== id)
  })),

  clear: () => set({ notifications: [] }),
}))

// Convenience functions for direct import
export const notify = {
  success: (message: string, options?: { title?: string; duration?: number }) =>
    useNotifications.getState().add('success', message, options),
  error: (message: string, options?: { title?: string; duration?: number }) =>
    useNotifications.getState().add('error', message, options),
  info: (message: string, options?: { title?: string; duration?: number }) =>
    useNotifications.getState().add('info', message, options),
  warning: (message: string, options?: { title?: string; duration?: number }) =>
    useNotifications.getState().add('warning', message, options),
}
