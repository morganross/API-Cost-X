import * as React from 'react'
import { Check } from 'lucide-react'

// Simple cn replacement - just concatenate class names
function cn(...classes: (string | boolean | undefined)[]) {
  return classes.filter(Boolean).join(' ')
}

interface CheckboxProps {
  checked: boolean
  onChange: (checked: boolean) => void
  label?: string
  /** Extra content rendered after the model label (e.g. score badges) */
  scoreSuffix?: React.ReactNode
  disabled?: boolean
  className?: string
  /** Data attribute for easier test/automation selection */
  dataTestId?: string
  /** Section context for grouping */
  section?: string
  /** DR Native model - styled bold purple */
  drNative?: boolean
  /** Raw numeric score value — written as data-score on the row for machine reading */
  dataScore?: number
}

export function Checkbox({
  checked,
  onChange,
  label,
  scoreSuffix,
  disabled = false,
  className,
  dataTestId,
  section,
  drNative = false,
  dataScore,
}: CheckboxProps) {
  const id = React.useId()
  // Generate auto testId from section and label if not provided
  const autoTestId = dataTestId || (section && label ? `${section}-${label}`.replace(/[:\s]/g, '-') : undefined)

  return (
    <label
      htmlFor={id}
      className={cn(
        'flex items-center gap-2 cursor-pointer select-none group',
        disabled && 'opacity-50 cursor-not-allowed',
        className
      )}
      data-testid={autoTestId}
      data-model={label}
      data-checked={checked}
      data-section={section}
      data-score={dataScore}
    >
      {/* Hidden input for accessibility / form semantics */}
      <input
        type="checkbox"
        id={id}
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="sr-only"
        data-model={label}
        data-testid={autoTestId ? `${autoTestId}-input` : undefined}
      />
      <div
        className={cn(
          'w-5 h-5 rounded border-2 flex items-center justify-center flex-shrink-0 transition-colors',
          'peer-focus-visible:ring-2 peer-focus-visible:ring-blue-500 peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-gray-900',
          checked ? 'bg-blue-500 border-blue-500' : 'bg-gray-700 border-gray-600',
          !disabled && 'group-hover:border-blue-400'
        )}
      >
        {checked && <Check className="w-3.5 h-3.5 text-white" strokeWidth={3} />}
      </div>
      {label && (
        <span className={cn(
          "text-sm flex items-center gap-2 flex-wrap",
          drNative ? "text-purple-400 font-bold" : "text-gray-300"
        )} data-testid={autoTestId ? `${autoTestId}-label` : undefined}>
          <span className="truncate">{label}</span>
          {scoreSuffix}
        </span>
      )}
    </label>
  )
}

interface CheckboxGroupProps {
  title: string
  enabled: boolean
  onEnabledChange: (enabled: boolean) => void
  children: React.ReactNode
  className?: string
  /** Data attribute for easier test/automation selection */
  dataTestId?: string
}

export function CheckboxGroup({
  title,
  enabled,
  onEnabledChange,
  children,
  dataTestId,
}: CheckboxGroupProps) {
  return (
    <div
      className="rounded-lg bg-gray-800/50 border border-gray-700"
      data-testid={dataTestId}
      data-section={title}
    >
      <div className="flex items-center justify-between p-3 border-b border-gray-700">
        <span className="font-semibold text-gray-200">{title}</span>
        <ToggleSwitch checked={enabled} onChange={onEnabledChange} label={title} />
      </div>
      {enabled && <div className="p-3">{children}</div>}
    </div>
  )
}

interface ToggleSwitchProps {
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  /** Label for accessibility and automation */
  label?: string
}

export function ToggleSwitch({ checked, onChange, disabled = false, label }: ToggleSwitchProps) {
  const id = React.useId()
  const testId = label ? `toggle-${label}`.replace(/[:\s]/g, '-') : undefined

  return (
    <label
      htmlFor={id}
      className={cn(
        'relative inline-flex items-center cursor-pointer',
        disabled && 'opacity-50 cursor-not-allowed'
      )}
      data-toggle={label}
      data-testid={testId}
      data-checked={checked}
    >
      {/* Hidden input for accessibility / form semantics */}
      <input
        type="checkbox"
        id={id}
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="sr-only"
        data-toggle={label}
        data-testid={testId ? `${testId}-input` : undefined}
      />
      <div
        className={cn(
          'relative w-11 h-6 rounded-full transition-colors',
          'peer-focus-visible:ring-2 peer-focus-visible:ring-blue-500 peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-gray-900',
          checked ? 'bg-blue-500' : 'bg-gray-600'
        )}
      >
        <span
          className={cn(
            'absolute top-1 left-1 w-4 h-4 bg-white rounded-full transition-transform',
            checked && 'translate-x-5'
          )}
        />
      </div>
    </label>
  )
}
