import { useEffect, useMemo, useState } from 'react'
import { useConfigStore } from '@/stores/config'
import { useModelCatalog } from '@/stores/modelCatalog'
import {
  generationRowsFromModels,
  groupModelsByProvider,
  sectionRowsFromModels,
  type GenerationRow,
  type SelectorRow,
  type ReportType,
} from './modelSelectionShared'

const EMPTY_GENERATION_ROW: GenerationRow = { reportType: 'fpf', provider: '', model: '' }
const EMPTY_SELECTOR_ROW: SelectorRow = { provider: '', model: '' }

export function usePresetModelSelection() {
  const config = useConfigStore()
  const { fpfModels, gptrModels, drModels, evalModels, combineModels } = useModelCatalog()

  const groupedModels = useMemo(
    () => ({
      fpf: groupModelsByProvider(fpfModels),
      gptr: groupModelsByProvider(gptrModels),
      dr: groupModelsByProvider(drModels),
      eval: groupModelsByProvider(evalModels),
      combine: groupModelsByProvider(combineModels),
    }),
    [combineModels, drModels, evalModels, fpfModels, gptrModels]
  )

  const [generationRows, setGenerationRows] = useState<GenerationRow[]>([EMPTY_GENERATION_ROW])
  const [evalRows, setEvalRows] = useState<SelectorRow[]>([EMPTY_SELECTOR_ROW])
  const [combineRows, setCombineRows] = useState<SelectorRow[]>([EMPTY_SELECTOR_ROW])

  useEffect(() => {
    setGenerationRows((prev) => {
      const nextRows = generationRowsFromModels(
        config.fpf.selectedModels,
        config.gptr.selectedModels,
        config.dr.selectedModels
      )
      const targetCount = Math.max(1, prev.length, nextRows.length)
      return Array.from({ length: targetCount }, (_, index) => {
        const selected = nextRows[index]
        const previous = prev[index]
        return selected?.model ? selected : previous ?? EMPTY_GENERATION_ROW
      })
    })
  }, [config.dr.selectedModels, config.fpf.selectedModels, config.gptr.selectedModels])

  useEffect(() => {
    setEvalRows((prev) => {
      const nextRows = sectionRowsFromModels(config.eval.judgeModels)
      const targetCount = Math.max(1, prev.length, nextRows.length)
      return Array.from({ length: targetCount }, (_, index) => {
        const selected = nextRows[index]
        const previous = prev[index]
        return selected?.model ? selected : previous ?? EMPTY_SELECTOR_ROW
      })
    })
  }, [config.eval.judgeModels])

  useEffect(() => {
    setCombineRows((prev) => {
      const nextRows = sectionRowsFromModels(config.combine.selectedModels)
      const targetCount = Math.max(1, prev.length, nextRows.length)
      return Array.from({ length: targetCount }, (_, index) => {
        const selected = nextRows[index]
        const previous = prev[index]
        return selected?.model ? selected : previous ?? EMPTY_SELECTOR_ROW
      })
    })
  }, [config.combine.selectedModels])

  const commitGenerationRows = (rows: GenerationRow[]) => {
    const selected = {
      fpf: [] as string[],
      gptr: [] as string[],
      dr: [] as string[],
    }

    rows.forEach((row) => {
      if (!row.model) return
      selected[row.reportType].push(row.model)
    })

    config.updateFpf({ selectedModels: selected.fpf, enabled: selected.fpf.length > 0 })
    config.updateGptr({ selectedModels: selected.gptr, enabled: selected.gptr.length > 0 })
    config.updateDr({ selectedModels: selected.dr, enabled: selected.dr.length > 0 })
  }

  const commitRows = (section: 'eval' | 'combine', rows: SelectorRow[]) => {
    const selected = rows.map((row) => row.model).filter(Boolean)
    if (section === 'eval') {
      config.updateEval({ judgeModels: selected, enabled: selected.length > 0 })
      return
    }
    config.updateCombine({ selectedModels: selected, enabled: selected.length > 0 })
  }

  const setGenerationReportType = (index: number, reportType: ReportType) => {
    setGenerationRows((prev) => {
      const rows = [...prev]
      rows[index] = { reportType, provider: '', model: '' }
      commitGenerationRows(rows)
      return rows
    })
  }

  const setGenerationProvider = (index: number, provider: string) => {
    setGenerationRows((prev) => {
      const rows = [...prev]
      rows[index] = { ...rows[index], provider, model: '' }
      commitGenerationRows(rows)
      return rows
    })
  }

  const setGenerationModel = (index: number, model: string) => {
    setGenerationRows((prev) => {
      const rows = [...prev]
      rows[index] = { ...rows[index], model }
      commitGenerationRows(rows)
      return rows
    })
  }

  const setSectionProvider = (section: 'eval' | 'combine', index: number, provider: string) => {
    const setRows = section === 'eval' ? setEvalRows : setCombineRows
    setRows((prev) => {
      const rows = [...prev]
      rows[index] = { provider, model: '' }
      commitRows(section, rows)
      return rows
    })
  }

  const setSectionModel = (section: 'eval' | 'combine', index: number, model: string) => {
    const setRows = section === 'eval' ? setEvalRows : setCombineRows
    setRows((prev) => {
      const rows = [...prev]
      rows[index] = { ...rows[index], model }
      commitRows(section, rows)
      return rows
    })
  }

  return {
    groupedModels,
    generationRows,
    evalRows,
    combineRows,
    addGenerationRow: () => setGenerationRows((prev) => [...prev, EMPTY_GENERATION_ROW]),
    addEvalRow: () => setEvalRows((prev) => [...prev, EMPTY_SELECTOR_ROW]),
    addCombineRow: () => setCombineRows((prev) => [...prev, EMPTY_SELECTOR_ROW]),
    setGenerationReportType,
    setGenerationProvider,
    setGenerationModel,
    setSectionProvider,
    setSectionModel,
  }
}

export type PresetModelSelectionController = ReturnType<typeof usePresetModelSelection>
