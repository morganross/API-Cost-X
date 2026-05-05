import type { ReactNode } from 'react'
import ExactPresetListView from './ExactPresetListView'
import type { MatrixViewSharedProps } from './ExactPresetMatrixShared'

export default function ExactPresetDropdownView({
  leadingSection,
  ...matrixProps
}: MatrixViewSharedProps & {
  leadingSection?: ReactNode
}) {
  return (
    <div className="space-y-6">
      {leadingSection ? <div>{leadingSection}</div> : null}
      <ExactPresetListView {...matrixProps} />
    </div>
  )
}
