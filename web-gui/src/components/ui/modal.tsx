import { useEffect, useRef, type ReactNode, type RefObject } from 'react'
import { cn } from '@/lib/utils'

interface ModalProps {
  open: boolean
  onClose: () => void
  children: ReactNode
  panelClassName?: string
  overlayClassName?: string
  initialFocusRef?: RefObject<HTMLElement | null>
}

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function getFocusableElements(container: HTMLElement | null): HTMLElement[] {
  if (!container) {
    return []
  }

  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) => !element.hasAttribute('disabled') && element.getAttribute('aria-hidden') !== 'true'
  )
}

export function Modal({
  open,
  onClose,
  children,
  panelClassName,
  overlayClassName,
  initialFocusRef,
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open || typeof document === 'undefined') {
      return
    }

    const previousActiveElement =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const focusPanel = () => {
      const explicitTarget = initialFocusRef?.current ?? null
      const [firstFocusable] = getFocusableElements(panelRef.current)
      ;(explicitTarget ?? firstFocusable ?? panelRef.current)?.focus()
    }

    const frame = window.requestAnimationFrame(focusPanel)

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }

      if (event.key !== 'Tab') {
        return
      }

      const focusable = getFocusableElements(panelRef.current)
      if (focusable.length === 0) {
        event.preventDefault()
        panelRef.current?.focus()
        return
      }

      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const activeElement =
        document.activeElement instanceof HTMLElement ? document.activeElement : null

      if (!event.shiftKey && activeElement === last) {
        event.preventDefault()
        first.focus()
      } else if (event.shiftKey && activeElement === first) {
        event.preventDefault()
        last.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)

    return () => {
      window.cancelAnimationFrame(frame)
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = previousOverflow
      previousActiveElement?.focus()
    }
  }, [initialFocusRef, onClose, open])

  if (!open) {
    return null
  }

  return (
    <div
      className={cn(
        'fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4 sm:items-center sm:p-6',
        overlayClassName
      )}
      onMouseDown={onClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        className={cn(
          'w-full rounded-lg border border-gray-700 bg-gray-800 shadow-2xl outline-none',
          panelClassName
        )}
        onMouseDown={(event) => event.stopPropagation()}
      >
        {children}
      </div>
    </div>
  )
}
