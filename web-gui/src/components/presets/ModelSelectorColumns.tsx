import { useState, type ReactNode } from 'react'
import { BarChart3, Combine as CombineIcon, Settings } from 'lucide-react'
import { Section } from '@/components/ui/section'
import {
  generationModelOptionLabel,
  modelLabel,
  providerLabel,
  type GenerationRow,
  type ReportType,
  type SelectorRow,
} from './modelSelectionShared'
import { usePresetModelSelection, type PresetModelSelectionController } from './usePresetModelSelection'

const REPORT_TYPE_OPTIONS: Array<{ value: ReportType; label: string }> = [
  { value: 'fpf', label: 'FPF' },
  { value: 'gptr', label: 'GPT-R' },
  { value: 'dr', label: 'DR' },
]

function FieldLabel({ children }: { children: ReactNode }) {
  return <label className="block text-sm font-medium text-gray-300 mb-2">{children}</label>
}

function SelectorRowShell({ children, first = false }: { children: ReactNode; first?: boolean }) {
  return <div className={first ? 'space-y-3' : 'space-y-3 border-t border-gray-700 pt-4'}>{children}</div>
}

const SELECT_CLASS =
  'w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500'

function AddRowButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1 px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded transition-colors text-gray-200"
    >
      Add more model
    </button>
  )
}

function PanelShell({
  title,
  icon,
  open,
  onToggle,
  children,
}: {
  title: string
  icon: ReactNode
  open: boolean
  onToggle: () => void
  children: ReactNode
}) {
  return (
    <Section title={title} icon={icon} expanded={open} onExpandedChange={onToggle}>
      {children}
    </Section>
  )
}

function GenerationSection({
  open,
  onToggle,
  groupedModels,
  rows,
  onReportTypeChange,
  onProviderChange,
  onModelChange,
  onAddRow,
}: {
  open: boolean
  onToggle: () => void
  groupedModels: Record<ReportType, Record<string, string[]>>
  rows: GenerationRow[]
  onReportTypeChange: (index: number, reportType: ReportType) => void
  onProviderChange: (index: number, provider: string) => void
  onModelChange: (index: number, modelKey: string) => void
  onAddRow: () => void
}) {
  return (
    <PanelShell title="Generation" icon={<Settings className="w-5 h-5" />} open={open} onToggle={onToggle}>
      {rows.map((row, index) => {
        const providerGroups = groupedModels[row.reportType]
        const providers = Object.keys(providerGroups).sort((a, b) => a.localeCompare(b))
        const modelsForProvider = row.provider ? providerGroups[row.provider] ?? [] : []

        return (
          <SelectorRowShell key={`gen-${index}`} first={index === 0}>
            <div className="space-y-1.5">
              <FieldLabel>Report Type</FieldLabel>
              <select
                value={row.reportType}
                onChange={(event) => onReportTypeChange(index, event.target.value as ReportType)}
                className={SELECT_CLASS}
              >
                {REPORT_TYPE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <FieldLabel>Provider</FieldLabel>
              <select
                value={row.provider}
                onChange={(event) => onProviderChange(index, event.target.value)}
                className={SELECT_CLASS}
              >
                <option value="">Select provider</option>
                {providers.map((provider) => (
                  <option key={provider} value={provider}>
                    {providerLabel(provider)}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <FieldLabel>Model</FieldLabel>
              <select
                value={row.model}
                onChange={(event) => onModelChange(index, event.target.value)}
                className={`${SELECT_CLASS} font-mono`}
                disabled={!row.provider}
              >
                <option value="">Select model</option>
                {modelsForProvider.map((modelKey) => (
                  <option key={modelKey} value={modelKey}>
                    {generationModelOptionLabel(row.reportType, modelKey)}
                  </option>
                ))}
              </select>
            </div>
          </SelectorRowShell>
        )
      })}

      <AddRowButton onClick={onAddRow} />
    </PanelShell>
  )
}

function StandardSection({
  title,
  open,
  onToggle,
  groupedModels,
  rows,
  onProviderChange,
  onModelChange,
  onAddRow,
}: {
  title: string
  open: boolean
  onToggle: () => void
  groupedModels: Record<string, string[]>
  rows: SelectorRow[]
  onProviderChange: (index: number, provider: string) => void
  onModelChange: (index: number, modelKey: string) => void
  onAddRow: () => void
}) {
  const providers = Object.keys(groupedModels).sort((a, b) => a.localeCompare(b))

  return (
    <PanelShell
      title={title}
      icon={title === 'Evaluation' ? <BarChart3 className="w-5 h-5" /> : <CombineIcon className="w-5 h-5" />}
      open={open}
      onToggle={onToggle}
    >
      {rows.map((row, index) => {
        const modelsForProvider = row.provider ? groupedModels[row.provider] ?? [] : []

        return (
          <SelectorRowShell key={`${title}-${index}`} first={index === 0}>
            <div className="space-y-1.5">
              <FieldLabel>Provider</FieldLabel>
              <select
                value={row.provider}
                onChange={(event) => onProviderChange(index, event.target.value)}
                className={SELECT_CLASS}
              >
                <option value="">Select provider</option>
                {providers.map((provider) => (
                  <option key={provider} value={provider}>
                    {providerLabel(provider)}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <FieldLabel>Model</FieldLabel>
              <select
                value={row.model}
                onChange={(event) => onModelChange(index, event.target.value)}
                className={SELECT_CLASS}
                disabled={!row.provider}
              >
                <option value="">Select model</option>
                {modelsForProvider.map((modelKey) => (
                  <option key={modelKey} value={modelKey}>
                    {modelLabel(modelKey)}
                  </option>
                ))}
              </select>
            </div>
          </SelectorRowShell>
        )
      })}

      <AddRowButton onClick={onAddRow} />
    </PanelShell>
  )
}

interface ModelSelectorColumnsProps {
  layout?: 'three-column' | 'four-column'
  leadingSection?: ReactNode
  controller?: PresetModelSelectionController
}

export default function ModelSelectorColumns({
  layout = 'three-column',
  leadingSection,
  controller,
}: ModelSelectorColumnsProps) {
  const [openSections, setOpenSections] = useState({
    gen: true,
    eval: true,
    combine: true,
  })
  const selection = controller ?? usePresetModelSelection()
  const {
    groupedModels,
    generationRows,
    evalRows,
    combineRows,
    addGenerationRow,
    addEvalRow,
    addCombineRow,
    setGenerationReportType,
    setGenerationProvider,
    setGenerationModel,
    setSectionProvider,
    setSectionModel,
  } = selection

  const isFourColumn = layout === 'four-column'

  return (
    <div
      className={
        isFourColumn
          ? 'mb-6 grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4'
          : 'mb-6 grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.95fr)_minmax(0,0.9fr)]'
      }
      data-testid="presets-model-selectors"
    >
      {leadingSection ? <div className="space-y-6">{leadingSection}</div> : null}

      <div className="space-y-6">
        <GenerationSection
          open={openSections.gen}
          onToggle={() => setOpenSections((prev) => ({ ...prev, gen: !prev.gen }))}
          groupedModels={{
            fpf: groupedModels.fpf,
            gptr: groupedModels.gptr,
            dr: groupedModels.dr,
          }}
          rows={generationRows}
          onReportTypeChange={setGenerationReportType}
          onProviderChange={setGenerationProvider}
          onModelChange={setGenerationModel}
          onAddRow={addGenerationRow}
        />
      </div>

      <div className="space-y-6">
        <StandardSection
          title="Evaluation"
          open={openSections.eval}
          onToggle={() => setOpenSections((prev) => ({ ...prev, eval: !prev.eval }))}
          groupedModels={groupedModels.eval}
          rows={evalRows}
          onProviderChange={(index, provider) => setSectionProvider('eval', index, provider)}
          onModelChange={(index, modelKey) => setSectionModel('eval', index, modelKey)}
          onAddRow={addEvalRow}
        />
      </div>

      <div className="space-y-6">
        <StandardSection
          title="Combine"
          open={openSections.combine}
          onToggle={() => setOpenSections((prev) => ({ ...prev, combine: !prev.combine }))}
          groupedModels={groupedModels.combine}
          rows={combineRows}
          onProviderChange={(index, provider) => setSectionProvider('combine', index, provider)}
          onModelChange={(index, modelKey) => setSectionModel('combine', index, modelKey)}
          onAddRow={addCombineRow}
        />
      </div>
    </div>
  )
}
