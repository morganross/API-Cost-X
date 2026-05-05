import * as React from 'react'
import { cn } from '../../lib/utils'
import { ChevronDown, Check } from 'lucide-react'

interface SelectOption {
  value: string
  label: string
}

interface SelectProps {
  value: string
  onChange: (value: string) => void
  options: SelectOption[]
  label?: string
  placeholder?: string
  disabled?: boolean
  className?: string
  /** Data-testid for automation */
  'data-testid'?: string
}

export function Select({
  value,
  onChange,
  options,
  label,
  placeholder = 'Select...',
  disabled = false,
  className,
  'data-testid': testId,
}: SelectProps) {
  const [isOpen, setIsOpen] = React.useState(false)
  const containerRef = React.useRef<HTMLDivElement>(null)
  const selectedOption = options.find(o => o.value === value)

  // Close dropdown when clicking outside
  React.useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Close on escape key
  React.useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsOpen(false)
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [])

  return (
    <div
      ref={containerRef}
      className={cn('space-y-1', className)}
      data-testid={testId}
      data-value={value}
      data-open={isOpen}
    >
      {label && <label className="block text-sm font-medium text-gray-400">{label}</label>}
      <div className="relative">
        <button
          type="button"
          onClick={() => !disabled && setIsOpen(!isOpen)}
          disabled={disabled}
          data-testid={testId ? `${testId}-trigger` : undefined}
          className={cn(
            'w-full flex items-center justify-between rounded-lg border border-gray-600 bg-gray-700 px-3 py-2',
            'text-sm text-gray-200 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500',
            'text-left',
            disabled && 'opacity-50 cursor-not-allowed'
          )}
        >
          <span className={!selectedOption ? 'text-gray-400' : ''}>
            {selectedOption?.label || placeholder}
          </span>
          <ChevronDown className={cn(
            'w-4 h-4 text-gray-400 transition-transform',
            isOpen && 'rotate-180'
          )} />
        </button>

        {isOpen && (
          <div
            className="absolute z-50 w-full mt-1 bg-gray-800 border border-gray-600 rounded-lg shadow-lg max-h-60 overflow-auto"
            data-testid={testId ? `${testId}-dropdown` : undefined}
          >
            {options.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => {
                  onChange(option.value)
                  setIsOpen(false)
                }}
                data-testid={testId ? `${testId}-option-${option.value}` : undefined}
                data-option-value={option.value}
                className={cn(
                  'w-full flex items-center justify-between px-3 py-2 text-sm text-left',
                  'hover:bg-gray-700 transition-colors',
                  option.value === value ? 'text-blue-400 bg-gray-750' : 'text-gray-200'
                )}
              >
                <span>{option.label}</span>
                {option.value === value && <Check className="w-4 h-4" />}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
