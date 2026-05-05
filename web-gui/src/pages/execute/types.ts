export type CellStatus = 'pending' | 'running' | 'completed' | 'error'

export interface ExecutionStatus {
  id: number;
  status: 'idle' | 'pending' | 'running' | 'paused' | 'completed' | 'completed_with_errors' | 'failed' | 'cancelled';
  started_at?: string;
  completed_at?: string;
  duration_seconds?: number;
  results?: string | object;
}

export interface EvalCell {
  model: string;
  document: string;
  evaluator: string;
  iteration?: number;
  status: CellStatus;
  score?: number;
  startTime?: string;
  endTime?: string;
  reasoning?: string;
}

export interface PairwiseCell {
  modelA: string;
  modelB: string;
  document: string;
  status: CellStatus;
  winner?: 'A' | 'B' | 'tie';
  elo?: number;
}

export interface GeneratedDoc {
  id: string;
  document: string;
  model: string;
  status: CellStatus;
  startTime?: string;
  endTime?: string;
  outputPath?: string;
}
