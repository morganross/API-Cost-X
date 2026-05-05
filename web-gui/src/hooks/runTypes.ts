export interface GeneratedDoc {
  id: string;
  model: string;
  source_doc_id: string;
  generator: string;
  iteration: number;
  duration_seconds: number | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface RunResponse {
  id: string;
  status: string;
  preset_id: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  config: any;
  generated_docs: GeneratedDoc[];
  eval_heatmap?: any;
  judge_quality?: any;
  timeline?: any;
  llm_calls?: any;
  rankings?: any;
  [key: string]: any;
}

export interface RunData extends RunResponse {}
