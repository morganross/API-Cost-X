import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import ExactPresetDropdownView from './exact/ExactPresetDropdownView'
import ExactPresetListView from './exact/ExactPresetListView'
import ExactPresetTableView from './exact/ExactPresetTableView'
import ExactPresetCardsView from './exact/ExactPresetCardsView'
import {
  PROVIDER_FILTER_ORDER,
  extractProviderFilterKey,
  type MatrixViewSharedProps,
  usePresetMatrixModels,
  type ProviderFilterKey,
  type MetricReportType,
} from './exact/ExactPresetMatrixShared'

type ViewMode = 'list' | 'table' | 'cards' | 'dropdown'

const DESKTOP_VIEW_OPTIONS: Array<{ value: ViewMode; label: string }> = [
  { value: 'list', label: 'List' },
  { value: 'table', label: 'Table' },
  { value: 'cards', label: 'Cards' },
  { value: 'dropdown', label: 'Dropdown' },
]

const MOBILE_VIEW_OPTIONS: Array<{ value: ViewMode; label: string }> = [
  { value: 'cards', label: 'Cards' },
  { value: 'dropdown', label: 'Dropdown' },
]

const VIEW_MODE_SESSION_KEY = 'apicostx-presets-view-mode'
const SELECT_CLASS =
  'w-full rounded border border-gray-600 bg-gray-700 px-3 py-2 text-sm text-gray-200 focus:border-blue-500 focus:outline-none'

function readStoredViewMode(): ViewMode | null {
  if (typeof window === 'undefined') return null
  const value = window.sessionStorage.getItem(VIEW_MODE_SESSION_KEY)
  return value === 'list' || value === 'table' || value === 'cards' || value === 'dropdown' ? value : null
}

function useIsMobilePresetViewport() {
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.innerWidth < 768
  })

  useEffect(() => {
    if (typeof window === 'undefined') return
    const handleResize = () => setIsMobile(window.innerWidth < 768)
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  return isMobile
}

export default function UnifiedPresetModelSection({
  leadingSection,
  onLeadingSectionVisibilityChange,
}: {
  leadingSection?: ReactNode
  onLeadingSectionVisibilityChange?: (visible: boolean) => void
}) {
  const isMobile = useIsMobilePresetViewport()
  const allowedViewOptions = isMobile ? MOBILE_VIEW_OPTIONS : DESKTOP_VIEW_OPTIONS
  const [preferredView, setPreferredView] = useState<ViewMode>(() => readStoredViewMode() ?? 'list')
  const [cardSortCol, setCardSortCol] = useState<string | null>(null)
  const [cardSortDir, setCardSortDir] = useState<'asc' | 'desc'>('asc')
  const [metricReportType, setMetricReportType] = useState<MetricReportType>('fpf')
  const [filterProviders, setFilterProviders] = useState<Set<ProviderFilterKey> | null>(null)
  const [showScored, setShowScored] = useState(true)
  const [showUnscored, setShowUnscored] = useState(false)
  const [showFree, setShowFree] = useState(true)
  const [hideKeyless, setHideKeyless] = useState(false)
  const [configuredKeyProviders, setConfiguredKeyProviders] = useState<Set<string>>(new Set())

  const { matrixModels } = usePresetMatrixModels()

  const distinctProviders = useMemo(() => {
    const providerSet = new Set(matrixModels.map(extractProviderFilterKey))
    return PROVIDER_FILTER_ORDER.filter((provider) => providerSet.has(provider))
  }, [matrixModels])

  const activeView = useMemo<ViewMode>(() => {
    const allowed = new Set(allowedViewOptions.map((option) => option.value))
    return allowed.has(preferredView) ? preferredView : allowedViewOptions[0].value
  }, [allowedViewOptions, preferredView])

  useEffect(() => {
    if (distinctProviders.length > 0 && filterProviders === null) {
      setFilterProviders(new Set(distinctProviders))
    }
  }, [distinctProviders, filterProviders])

  useEffect(() => {
    onLeadingSectionVisibilityChange?.(activeView === 'dropdown')
  }, [activeView, onLeadingSectionVisibilityChange])

  const handleViewChange = (nextView: ViewMode) => {
    setPreferredView(nextView)
    if (typeof window !== 'undefined') {
      window.sessionStorage.setItem(VIEW_MODE_SESSION_KEY, nextView)
    }
  }

  const handleColSort = (col: string) => {
    if (cardSortCol === col) {
      setCardSortDir((direction) => (direction === 'asc' ? 'desc' : 'asc'))
      return
    }
    setCardSortCol(col)
    setCardSortDir('asc')
  }

  const clearFilters = () => {
    if (distinctProviders.length > 0) setFilterProviders(new Set(distinctProviders))
    setShowScored(true)
    setShowUnscored(true)
    setShowFree(true)
    setHideKeyless(false)
  }

  const matrixViewProps: MatrixViewSharedProps = {
    matrixModels,
    distinctProviders,
    configuredKeyProviders,
    filterProviders,
    showScored,
    showUnscored,
    showFree,
    hideKeyless,
    cardSortCol,
    cardSortDir,
    metricReportType,
    setMetricReportType,
    setFilterProviders,
    setShowScored,
    setShowUnscored,
    setShowFree,
    setHideKeyless,
    handleColSort,
    clearFilters,
  }

  const viewBody =
    activeView === 'dropdown' ? (
      <ExactPresetDropdownView leadingSection={leadingSection} {...matrixViewProps} />
    ) : activeView === 'table' ? (
      <ExactPresetTableView {...matrixViewProps} />
    ) : activeView === 'cards' ? (
      <ExactPresetCardsView {...matrixViewProps} />
    ) : (
      <ExactPresetListView {...matrixViewProps} />
    )

  return (
    <div className="mb-6 space-y-4" data-testid="preset14-unified-model-section" data-current-view={activeView}>
      <div
        className="hidden"
        data-testid="preset14-state"
        data-current-view={activeView}
        data-is-mobile={isMobile}
        data-allowed-views={JSON.stringify(allowedViewOptions.map((option) => option.value))}
      />

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-end">
        <label className="text-sm font-medium text-gray-300 sm:mr-2">View</label>
        <select
          value={activeView}
          onChange={(event) => handleViewChange(event.target.value as ViewMode)}
          className={cn(SELECT_CLASS, 'w-full sm:w-52')}
          data-testid="preset14-view-selector"
        >
          {allowedViewOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      {viewBody}
    </div>
  )
}
