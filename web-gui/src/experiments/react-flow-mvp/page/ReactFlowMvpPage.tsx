import '@xyflow/react/dist/style.css'

import { ReactFlowProvider } from '@xyflow/react'
import FlowLabCanvas from '../components/FlowLabCanvas'

export default function ReactFlowMvpPage() {
  return (
    <div className="flex min-h-[calc(100dvh-var(--apicostx-page-sticky-top,173px)-2rem)] flex-col gap-4">
      <div className="max-w-4xl">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Experiment</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-foreground">Saved Flows</h1>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-muted-foreground">
          A fully separate workflow editor for chaining preset runs on a blank canvas. Root nodes keep using the input
          docs already saved inside their presets, while connected nodes consume upstream output docs.
        </p>
      </div>

      <ReactFlowProvider>
        <FlowLabCanvas />
      </ReactFlowProvider>
    </div>
  )
}
