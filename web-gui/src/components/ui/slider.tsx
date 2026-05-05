import { cn } from '../../lib/utils'

interface SliderProps {
  value: number
  onChange: (value: number) => void
  min: number
  max: number
  step?: number
  label: string
  displayValue?: string | number
  disabled?: boolean
  className?: string
}

export function Slider({
  value,
  onChange,
  min,
  max,
  step = 1,
  label,
  displayValue,
  disabled = false,
  className,
}: SliderProps) {
  const percentage = ((value - min) / (max - min)) * 100

  return (
    <div className={cn('space-y-1', className)}>
      <div className="flex justify-between text-sm">
        <span className="text-gray-300">{label}</span>
        <span className="font-mono text-blue-400">
          {displayValue !== undefined ? displayValue : value}
        </span>
      </div>
      <div className="relative">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          disabled={disabled}
          className={cn(
            'w-full h-2 rounded-lg appearance-none cursor-pointer',
            'bg-gray-700',
            '[&::-webkit-slider-thumb]:appearance-none',
            '[&::-webkit-slider-thumb]:w-4',
            '[&::-webkit-slider-thumb]:h-4',
            '[&::-webkit-slider-thumb]:rounded-full',
            '[&::-webkit-slider-thumb]:bg-blue-500',
            '[&::-webkit-slider-thumb]:cursor-pointer',
            '[&::-webkit-slider-thumb]:hover:bg-blue-400',
            '[&::-moz-range-thumb]:w-4',
            '[&::-moz-range-thumb]:h-4',
            '[&::-moz-range-thumb]:rounded-full',
            '[&::-moz-range-thumb]:bg-blue-500',
            '[&::-moz-range-thumb]:border-0',
            '[&::-moz-range-thumb]:cursor-pointer',
            disabled && 'opacity-50 cursor-not-allowed'
          )}
          style={{
            background: `linear-gradient(to right, #3b82f6 0%, #3b82f6 ${percentage}%, #374151 ${percentage}%, #374151 100%)`,
          }}
        />
      </div>
      <div className="flex justify-between text-xs text-gray-500">
        <span>{min}</span>
        <span>{max}</span>
      </div>
    </div>
  )
}
