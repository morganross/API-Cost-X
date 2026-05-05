import { Link } from 'react-router-dom'
import { Clock, FileText } from 'lucide-react'
import RunStatusBadge from './RunStatusBadge'

interface RunCardProps {
  run: {
    id: string
    name: string
    status: 'pending' | 'running' | 'paused' | 'completed' | 'completed_with_errors' | 'failed' | 'cancelled'
    pause_requested?: number
    mode: 'full' | 'generate_only' | 'evaluate_only'
    documentCount: number
    createdAt: string
    completedAt?: string
  }
}

export default function RunCard({ run }: RunCardProps) {
  return (
    <Link
      to={`/runs/${run.id}`}
      className="block p-4 hover:bg-accent transition-colors"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div>
            <h3 className="font-medium text-foreground">{run.name}</h3>
            <p className="text-sm text-muted-foreground">{run.id}</p>
          </div>
        </div>
        <RunStatusBadge status={run.status} pauseRequested={run.pause_requested} />
      </div>

      <div className="mt-3 flex items-center gap-4 text-sm text-muted-foreground">
        <span className="flex items-center gap-1">
          <FileText className="h-4 w-4" />
          {run.documentCount} docs
        </span>
        <span className="flex items-center gap-1">
          <Clock className="h-4 w-4" />
          {new Date(run.createdAt).toLocaleDateString()}
        </span>
        <span className="capitalize">{run.mode.replace('_', ' ')}</span>
      </div>
    </Link>
  )
}
