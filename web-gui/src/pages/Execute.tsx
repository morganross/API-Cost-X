import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Play, Pause, Square, AlertCircle, Activity, Clock,
  FileText, Users, ChevronDown, Timer,
  XCircle, CheckCircle, Loader2, RefreshCw, Download
} from 'lucide-react';
import LogViewer from '../components/execution/LogViewer';
import type { Run } from '../api';
import { ApiError, runsApi } from '../api';
import { formatTime, computeEndTime } from './execute/utils';
import SourceDocSection from './execute/SourceDocSection';
import { API_BASE_URL, authenticatedFetch } from '../api/client';
import type { DocumentEvalDetail, PairwiseResults, RunLiveSummary, RunResumeInfo, SourceDocResult } from '../api/runs';
import type { RunEstimateSnapshot } from '../api/presets';

interface Preset {
  id: string;
  name: string;
  description?: string;
  document_count?: number;
  model_count?: number;
  documents?: string[];
  evaluators?: string[];
  log_level?: string;
  output_destination?: 'github' | 'library' | 'none';
  output_filename_template?: string;
  skip_existing_outputs?: boolean;
  github_connection_id?: string;
  github_output_path?: string;
  github_input_paths?: string[];
  general_config?: {
    save_run_logs?: boolean;
    enable_logging?: boolean;
    run_estimate?: RunEstimateSnapshot;
  };
}

interface PhaseProgressRow {
  key: string;
  label: string;
  color: string;
  actual: number;
  total: number;
}

const EMPTY_PHASE_PROGRESS: PhaseProgressRow[] = [
  { key: 'generation', label: 'Generation', color: '#3b82f6', actual: 0, total: 0 },
  { key: 'single-eval', label: 'Single Eval', color: '#8b5cf6', actual: 0, total: 0 },
  { key: 'pre-pairwise', label: 'Pre-Combine Pairwise', color: '#a855f7', actual: 0, total: 0 },
  { key: 'combine', label: 'Combine', color: '#f59e0b', actual: 0, total: 0 },
  { key: 'post-pairwise', label: 'Post-Combine Pairwise', color: '#22c55e', actual: 0, total: 0 },
];

const LIVE_RUN_POLL_DELAYS_MS = [2000, 4000, 8000, 15000] as const;
const LIVE_RUN_FULL_RECONCILE_MIN_MS = 30000;

function isRunActiveStatus(status?: Run['status']): boolean {
  return status === 'running' || status === 'pending';
}

function isRunTerminalStatus(status?: Run['status']): boolean {
  return status === 'completed' || status === 'completed_with_errors' || status === 'failed' || status === 'cancelled';
}

function getLiveSummarySignature(summary: RunLiveSummary): string {
  return JSON.stringify({
    status: summary.status,
    progress: summary.progress,
    started_at: summary.started_at,
    completed_at: summary.completed_at,
    error_message: summary.error_message,
    pause_requested: summary.pause_requested,
    resume_count: summary.resume_count,
    current_call: summary.fpf_stats?.current_call,
    current_phase: summary.fpf_stats?.current_phase,
    last_error: summary.fpf_stats?.last_error,
  });
}

function countEvalCalls(detailMap?: Record<string, DocumentEvalDetail>): number {
  if (!detailMap) return 0;
  return Object.values(detailMap).reduce((sum, detail) => sum + (detail?.evaluations?.length || 0), 0);
}

function countPairwiseCalls(result?: PairwiseResults | null): number {
  if (!result) return 0;
  if (typeof result.total_comparisons === 'number') return result.total_comparisons;
  return result.comparisons?.length || 0;
}

function getRunEstimate(run: Run | null, preset: Preset | null): RunEstimateSnapshot | null {
  return run?.run_estimate ?? preset?.general_config?.run_estimate ?? null;
}

function getActualPhaseCounts(run: Run | null) {
  const empty = {
    generation: 0,
    singleEval: 0,
    preCombinePairwise: 0,
    combine: 0,
    postCombinePairwise: 0,
  };

  if (!run) return empty;

  const sourceDocResults = Object.values(run.source_doc_results || {});
  if (sourceDocResults.length === 0) {
    return {
      generation: run.generated_docs?.length || 0,
      singleEval: countEvalCalls(run.pre_combine_evals_detailed),
      preCombinePairwise: countPairwiseCalls(run.pairwise_results),
      combine: run.combined_doc_ids?.length || 0,
      postCombinePairwise: countPairwiseCalls(run.post_combine_pairwise),
    };
  }

  return sourceDocResults.reduce((totals, sourceDoc) => ({
    generation: totals.generation + (sourceDoc.generated_doc_count ?? sourceDoc.generated_docs?.length ?? 0),
    singleEval: totals.singleEval + Math.max(
      countEvalCalls(sourceDoc.single_eval_detailed),
      sourceDoc.single_eval_score_count ?? Object.keys(sourceDoc.single_eval_scores || {}).length
    ),
    preCombinePairwise: totals.preCombinePairwise + countPairwiseCalls(sourceDoc.pairwise_results),
    combine: totals.combine + (sourceDoc.combined_doc_count ?? ((sourceDoc.combined_docs?.length || 0) || (sourceDoc.combined_doc ? 1 : 0))),
    postCombinePairwise: totals.postCombinePairwise + countPairwiseCalls(sourceDoc.post_combine_pairwise),
  }), empty);
}

function getVisiblePairwiseCount(run: Run | null): number {
  const actual = getActualPhaseCounts(run)
  return actual.preCombinePairwise + actual.postCombinePairwise
}

function isEmptyValue(val: unknown): boolean {
  if (val === null || val === undefined) return true;
  if (Array.isArray(val)) return val.length === 0;
  if (typeof val === 'object') return Object.keys(val as object).length === 0;
  return false;
}

function mergeSourceDocResult(
  prev: SourceDocResult | undefined,
  updated: Partial<SourceDocResult>
): SourceDocResult {
  const merged: any = { ...(prev || {}), ...updated };
  const preserveFields = [
    'generated_docs',
    'single_eval_scores',
    'single_eval_detailed',
    'pairwise_results',
    'combined_doc',
    'combined_docs',
    'post_combine_eval_scores',
    'post_combine_pairwise',
    'timeline_events',
    'errors',
    'eval_deviations',
  ];

  for (const field of preserveFields) {
    const prevVal = (prev as any)?.[field];
    const nextVal = (updated as any)?.[field];
    if (isEmptyValue(nextVal) && !isEmptyValue(prevVal)) {
      merged[field] = prevVal;
    }
  }

  return merged as SourceDocResult;
}

export default function Execute() {
  const { runId } = useParams<{ runId?: string }>();
  const navigate = useNavigate();
  const [presets, setPresets] = useState<Preset[]>([]);
  const [selectedPreset, setSelectedPreset] = useState<Preset | null>(null);
  const [fullPresetData, setFullPresetData] = useState<Preset | null>(null);
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [currentRun, setCurrentRun] = useState<Run | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [isReevaluating, setIsReevaluating] = useState(false);
  const [isPausing, setIsPausing] = useState(false);
  const [isResuming, setIsResuming] = useState(false);
  const [resumeInfo, setResumeInfo] = useState<RunResumeInfo | null>(null);
  const [resumeInfoRunId, setResumeInfoRunId] = useState<string | null>(null);
  const [runningRunsCount, setRunningRunsCount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showLogModal, setShowLogModal] = useState(false);
  const [isDownloadingLog, setIsDownloadingLog] = useState(false);
  const [runPollingNotice, setRunPollingNotice] = useState<string | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const livePollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const livePollDelayIndexRef = useRef(0);
  const lastLiveSummarySignatureRef = useRef('');
  const consecutiveUnchangedLivePollsRef = useRef(0);
  const consecutiveLivePollFailuresRef = useRef(0);
  const lastFullRunRefreshAtRef = useRef(0);
  const livePollInFlightRef = useRef(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const effectiveRunId = currentRun?.id || runId;
    if (!effectiveRunId) return;
    window.sessionStorage.setItem('apicostxCurrentRunId', effectiveRunId);
  }, [currentRun?.id, runId]);

  useEffect(() => {
    if (!currentRun?.id) {
      setResumeInfo(null);
      setResumeInfoRunId(null);
      return;
    }

    if (isRunActiveStatus(currentRun.status)) {
      setResumeInfo(null);
      setResumeInfoRunId(currentRun.id);
      return;
    }

    let cancelled = false;
    const targetRunId = currentRun.id;

    runsApi.getResumeInfo(targetRunId)
      .then((info) => {
        if (cancelled) return;
        setResumeInfo(info);
        setResumeInfoRunId(targetRunId);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('Failed to load resume info:', err);
        setResumeInfo(null);
        setResumeInfoRunId(targetRunId);
      });

    return () => {
      cancelled = true;
    };
  }, [
    currentRun?.id,
    currentRun?.status,
    currentRun?.completed_at,
    currentRun?.pause_requested,
    currentRun?.resume_count,
  ]);

  const fetchRunForDisplay = useCallback(async (id: string, statusHint?: Run['status']): Promise<Run> => {
    if (isRunTerminalStatus(statusHint)) {
      return runsApi.getSnapshot(id);
    }
    return runsApi.getExecutionView(id);
  }, []);

  // Helper to check if a value is "empty" (null, undefined, empty object, or empty array)
  const isEmpty = (val: unknown): boolean => {
    return isEmptyValue(val);
  };

  // Merge run updates while preserving non-empty data
  // If the new value is empty but the old value had data, keep the old data
  const mergeRun = (prev: Run | null, updated: Run): Run => {
    const merged: any = { ...(prev || {}), ...updated };

    // Fields that should be preserved if the update is empty but prev had data
    const preserveFields = [
      'fpf_stats',
      'pre_combine_evals_detailed',
      'post_combine_evals_detailed',
      'criteria_list',
      'evaluator_list',
      'timeline_events',
      'generated_docs',
      'post_combine_evals',
      'pairwise_results',
      'tasks',
      'source_doc_results',
    ];

    for (const field of preserveFields) {
      const prevVal = (prev as any)?.[field];
      const newVal = (updated as any)?.[field];
      // If new value is empty but prev had data, keep prev
      if (isEmpty(newVal) && !isEmpty(prevVal)) {
        merged[field] = prevVal;
      }
    }

    return merged;
  };

  const mergeRunLiveSummary = (prev: Run | null, summary: RunLiveSummary): Run | null => {
    if (!prev) return prev;

    const hasSummarySourceDocResults =
      summary.source_doc_results &&
      Object.keys(summary.source_doc_results).length > 0;

    const mergedSourceDocResults: Run['source_doc_results'] = hasSummarySourceDocResults
      ? {
          ...(prev.source_doc_results || {}),
          ...Object.fromEntries(
            Object.entries(summary.source_doc_results || {}).map(([sourceDocId, nextSourceDocResult]) => [
              sourceDocId,
              mergeSourceDocResult(prev.source_doc_results?.[sourceDocId], nextSourceDocResult),
            ])
          ),
        } as Run['source_doc_results']
      : prev.source_doc_results;

    return {
      ...prev,
      status: summary.status ?? prev.status,
      progress: summary.progress ?? prev.progress,
      started_at: summary.started_at ?? prev.started_at,
      completed_at: summary.completed_at ?? prev.completed_at,
      error_message: summary.error_message ?? prev.error_message,
      pause_requested: summary.pause_requested ?? prev.pause_requested,
      resume_count: summary.resume_count ?? prev.resume_count,
      fpf_stats: summary.fpf_stats ?? prev.fpf_stats,
      ...(hasSummarySourceDocResults ? { source_doc_results: mergedSourceDocResults } : {}),
    };
  };

  const handleRunUpdate = useCallback((updatedRun: Run) => {
    if (!updatedRun?.id) return;

    // If run just completed, re-fetch from API to get full data (WebSocket may have incomplete data)
    if (updatedRun.status === 'completed' || updatedRun.status === 'completed_with_errors' || updatedRun.status === 'failed' || updatedRun.status === 'cancelled') {
      setIsRunning(false);
      setRunPollingNotice(null);
      runsApi.getSnapshot(updatedRun.id).then(fullRun => {
        setCurrentRun(prev => mergeRun(prev, fullRun));
      }).catch(err => {
        console.error('Failed to re-fetch run on completion:', err);
        // Fall back to WebSocket data
        setCurrentRun(prev => mergeRun(prev, updatedRun));
      });
      return;
    }

    setCurrentRun(prev => mergeRun(prev, updatedRun));
    // Update running state based on status
    if (updatedRun.status === 'running' || updatedRun.status === 'pending') {
      setIsRunning(true);
    }
  }, []);

  const handleStatsUpdate = useCallback((stats: any) => {
    setCurrentRun(prev => (prev ? { ...prev, fpf_stats: stats } : prev));
  }, []);

  const handleGenComplete = useCallback((genDoc: any) => {
    // Add generated doc to local state for immediate heatmap row display
    setCurrentRun(prev => {
      if (!prev) return prev;
      const generatedDocs = prev.generated_docs || [];
      // Avoid duplicates
      if (generatedDocs.some((d: any) => d.id === genDoc.id)) {
        return prev;
      }
      return { ...prev, generated_docs: [...generatedDocs, genDoc] };
    });
  }, []);

  const handleTaskUpdate = useCallback((task: any) => {
    setCurrentRun(prev => {
      if (!prev) return prev;
      const tasks = (prev.tasks || []).map((t: any) => (t.id === task.id ? { ...t, ...task } : t));
      return { ...prev, tasks };
    });
  }, []);

  const handleTasksInit = useCallback((tasks: any[]) => {
    setCurrentRun(prev => (prev ? { ...prev, tasks } : prev));
  }, []);

  // Poll for run updates while running (no WebSockets per LAWS)
  useEffect(() => {
    if (livePollTimeoutRef.current) {
      clearTimeout(livePollTimeoutRef.current);
      livePollTimeoutRef.current = null;
    }

    livePollDelayIndexRef.current = 0;
    lastLiveSummarySignatureRef.current = '';
    consecutiveUnchangedLivePollsRef.current = 0;
    consecutiveLivePollFailuresRef.current = 0;
    livePollInFlightRef.current = false;
    setRunPollingNotice(null);

    if (!currentRun?.id || !isRunning) return;

    const runId = currentRun.id;
    let cancelled = false;

    const scheduleNextPoll = () => {
      if (cancelled) return;
      const delay = LIVE_RUN_POLL_DELAYS_MS[livePollDelayIndexRef.current] ?? LIVE_RUN_POLL_DELAYS_MS[LIVE_RUN_POLL_DELAYS_MS.length - 1];
      livePollTimeoutRef.current = setTimeout(pollRun, delay);
    };

    const refreshFullRun = async () => {
      const fullRun = await runsApi.getExecutionView(runId);
      if (cancelled) return null;

      lastFullRunRefreshAtRef.current = Date.now();
      setCurrentRun(prev => mergeRun(prev, fullRun));

      if (!isRunActiveStatus(fullRun.status)) {
        setIsRunning(false);
        setRunPollingNotice(null);
      }

      return fullRun;
    };

    const pollRun = async () => {
      if (cancelled || livePollInFlightRef.current) {
        return;
      }

      livePollInFlightRef.current = true;
      let shouldScheduleNextPoll = true;

      try {
        const summary = await runsApi.getLiveSummary(runId);
        if (cancelled) {
          shouldScheduleNextPoll = false;
          return;
        }

        consecutiveLivePollFailuresRef.current = 0;
        setCurrentRun(prev => mergeRunLiveSummary(prev, summary));

        if (!isRunActiveStatus(summary.status)) {
          shouldScheduleNextPoll = false;
          setIsRunning(false);
          setRunPollingNotice(null);
          await refreshFullRun();
          return;
        }

        const signature = getLiveSummarySignature(summary);
        const unchangedCount = signature === lastLiveSummarySignatureRef.current
          ? consecutiveUnchangedLivePollsRef.current + 1
          : 0;

        lastLiveSummarySignatureRef.current = signature;
        consecutiveUnchangedLivePollsRef.current = unchangedCount;

        if (unchangedCount === 0) {
          livePollDelayIndexRef.current = 0;
          setRunPollingNotice(null);
        } else {
          livePollDelayIndexRef.current = Math.min(
            livePollDelayIndexRef.current + 1,
            LIVE_RUN_POLL_DELAYS_MS.length - 1
          );

          if (unchangedCount >= 2) {
            setRunPollingNotice('Run looks unchanged, polling more cautiously while checking for fresh progress.');
          }
        }

        const shouldReconcile =
          unchangedCount >= 3 &&
          Date.now() - lastFullRunRefreshAtRef.current >= LIVE_RUN_FULL_RECONCILE_MIN_MS;

        if (shouldReconcile) {
          const fullRun = await refreshFullRun();
          if (!fullRun || !isRunActiveStatus(fullRun.status)) {
            shouldScheduleNextPoll = false;
            return;
          }
          livePollDelayIndexRef.current = 0;
          lastLiveSummarySignatureRef.current = '';
          consecutiveUnchangedLivePollsRef.current = 0;
          setRunPollingNotice(null);
        }
      } catch (err) {
        console.error('Live run summary poll failed:', err);
        consecutiveLivePollFailuresRef.current += 1;
        livePollDelayIndexRef.current = Math.min(
          livePollDelayIndexRef.current + 1,
          LIVE_RUN_POLL_DELAYS_MS.length - 1
        );

        if (
          consecutiveLivePollFailuresRef.current >= 3 &&
          Date.now() - lastFullRunRefreshAtRef.current >= LIVE_RUN_FULL_RECONCILE_MIN_MS
        ) {
          try {
            const fullRun = await refreshFullRun();
            if (!fullRun || !isRunActiveStatus(fullRun.status)) {
              shouldScheduleNextPoll = false;
              return;
            }
          } catch (refreshError) {
            console.error('Full run reconcile after summary failures failed:', refreshError);
          }
        }
      } finally {
        livePollInFlightRef.current = false;
        if (shouldScheduleNextPoll && !cancelled) {
          scheduleNextPoll();
        }
      }
    };

    lastFullRunRefreshAtRef.current = Date.now();
    scheduleNextPoll();

    return () => {
      cancelled = true;
      livePollInFlightRef.current = false;
      if (livePollTimeoutRef.current) {
        clearTimeout(livePollTimeoutRef.current);
        livePollTimeoutRef.current = null;
      }
    };
  }, [currentRun?.id, isRunning]);

  // Load run from URL if runId is provided (initial load only - WebSocket handles updates)
  useEffect(() => {
    if (runId) {
      let cancelled = false;

      const fetchPresetForRun = async (loadedRun: Run) => {
        if (!loadedRun.preset_id) return;
        try {
          const presetResponse = await authenticatedFetch(`${API_BASE_URL}/presets/${loadedRun.preset_id}`);
          if (presetResponse.ok && !cancelled) {
            const presetData = await presetResponse.json();
            setFullPresetData(presetData);
          }
        } catch (err) {
          console.error('Failed to fetch preset for run:', err);
        }
      };

      const loadRun = async () => {
        try {
          const summary = await runsApi.getLiveSummary(runId);
          if (cancelled) return;

          const initialRun = runsApi.mapRun(summary);
          setCurrentRun(prev => mergeRun(prev, initialRun));
          setIsRunning(summary.status === 'running' || summary.status === 'pending');
          setError(null);

          await fetchPresetForRun(initialRun);

          try {
            const displayRun = await fetchRunForDisplay(runId, summary.status);
            if (!cancelled) {
              setCurrentRun(prev => mergeRun(prev, displayRun));
            }
          } catch (displayErr) {
            console.error('Failed to fetch expanded run view after summary load:', displayErr);
          }
          return;
        } catch (err) {
          console.error('Failed to load run summary:', err);
          try {
            const snapshotRun = await runsApi.getSnapshot(runId);
            if (cancelled) return;
            setCurrentRun(prev => mergeRun(prev, snapshotRun));
            setIsRunning(false);
            setError(null);
            await fetchPresetForRun(snapshotRun);
            return;
          } catch (snapshotErr) {
            console.error('Failed to load run snapshot fallback:', snapshotErr);
            if (cancelled) return;
            setCurrentRun(null);
            setIsRunning(false);
            if (err instanceof ApiError && err.status === 404 && runId) {
              setError('Run not found. Redirected to the main execute page.');
              navigate('/execute', { replace: true });
              return;
            }
            setError('Failed to load run');
          }
        }
      };

      loadRun();

      return () => {
        cancelled = true;
      };
    }
  }, [fetchRunForDisplay, navigate, runId]);

  useEffect(() => {
    if (runId) {
      return;
    }

    let cancelled = false;

    const loadLatestRun = async () => {
      try {
        const runs = await runsApi.list({ limit: 1 });
        if (cancelled || runs.length === 0) {
          return;
        }
        navigate(`/execute/${runs[0].id}`, { replace: true });
      } catch (err) {
        console.error('Failed to resolve latest run for execute page:', err);
      }
    };

    loadLatestRun();

    return () => {
      cancelled = true;
    };
  }, [navigate, runId]);

  // Fetch running runs count
  const fetchRunningCount = useCallback(async () => {
    try {
      const res = await authenticatedFetch(`${API_BASE_URL}/runs/count?status=running`);
      if (res.ok) {
        const data = await res.json();
        setRunningRunsCount(data.total);
      }
    } catch (err) {
      console.error('Failed to fetch running count:', err);
    }
  }, []);

  // Fetch running count once on load, then poll every 10s only while this run is active
  useEffect(() => {
    fetchRunningCount();
    if (!isRunning) {
      return;
    }
    const interval = setInterval(fetchRunningCount, 10000);
    return () => clearInterval(interval);
  }, [fetchRunningCount, isRunning]);

  // Fetch presets on mount
  useEffect(() => {
    authenticatedFetch(`${API_BASE_URL}/presets`)
      .then(res => res.json())
      .then(data => {
        const presetList = data.items || data.presets || data || [];
        setPresets(presetList);
        if (presetList.length > 0 && !selectedPreset) {
          setSelectedPreset(presetList[0]);
        }
      })
      .catch(err => {
        console.error('Failed to load presets:', err);
        setError('Failed to load presets');
      });
  }, []);

  useEffect(() => {
    if (!selectedPreset?.id) {
      setFullPresetData(null);
      return;
    }

    let cancelled = false;

    authenticatedFetch(`${API_BASE_URL}/presets/${selectedPreset.id}`)
      .then(async (response) => {
        if (!response.ok) return null;
        return response.json();
      })
      .then((presetData) => {
        if (!cancelled && presetData) {
          setFullPresetData(presetData);
        }
      })
      .catch((err) => {
        console.error('Failed to load preset details for execute page:', err);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedPreset?.id]);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const startExecution = async () => {
    if (!selectedPreset) return;

    setError(null);
    setIsRunning(true);
    setRunPollingNotice(null);
    setCurrentRun(null);

    try {
      // Fetch full preset to get documents
      const presetResponse = await authenticatedFetch(`${API_BASE_URL}/presets/${selectedPreset.id}`);
      if (!presetResponse.ok) {
        throw new Error('Failed to fetch preset details');
      }
      const presetData = await presetResponse.json();
      setFullPresetData(presetData);

      // Create a new run
      const hasGitHubOutput = Boolean(presetData.github_connection_id && presetData.github_output_path);
      const outputDestination = hasGitHubOutput ? 'github' : (presetData.output_destination || 'library');

      const createResponse = await authenticatedFetch(`${API_BASE_URL}/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: `${selectedPreset.name} - ${new Date().toLocaleString()}`,
          preset_id: selectedPreset.id,
          document_ids: presetData.documents || [],
          // Output destination fields from preset (fallback to derived destination)
          output_destination: outputDestination,
          output_filename_template: presetData.output_filename_template || '{source_doc_name}_{winner_model}_{timestamp}',
          skip_existing_outputs: presetData.skip_existing_outputs ?? true,
          prepend_source_first_line_frontmatter: presetData.prepend_source_first_line_frontmatter ?? false,
          github_connection_id: presetData.github_connection_id || undefined,
          github_output_path: presetData.github_output_path || undefined,
          github_input_paths: presetData.github_input_paths || undefined,

        })
      });

      if (!createResponse.ok) {
        const errorData = await createResponse.json().catch(() => ({}));
        const createDetail = typeof errorData.detail === 'string' ? errorData.detail : errorData.detail?.[0]?.msg;
        throw new Error(createDetail || 'Failed to create run');
      }

      const runData = await createResponse.json();
      const runId = runData.id;

      setCurrentRun(prev => {
        const merged: any = { ...(prev || {}), ...runData };
        if (!runData.fpf_stats && prev?.fpf_stats) {
          merged.fpf_stats = prev.fpf_stats;
        }
        return merged;
      });

      // Wait for React to render and WebSocket to connect before starting
      // This prevents the race condition where stats are broadcast before WS connects
      await new Promise(resolve => setTimeout(resolve, 500));

      // Start the run
      const startResponse = await authenticatedFetch(`${API_BASE_URL}/runs/${runId}/start`, {
        method: 'POST'
      });

      if (!startResponse.ok) {
        const startErrorData = await startResponse.json().catch(() => ({}));
        const startDetail = typeof startErrorData.detail === 'string' ? startErrorData.detail : startErrorData.detail?.[0]?.msg;
        throw new Error(startDetail || 'Failed to start run');
      }

      // Re-fetch the lighter execution view so source_doc_results appears immediately
      const updatedRun = await runsApi.getExecutionView(runId);
      setCurrentRun(prev => mergeRun(prev, updatedRun));
      console.log('[Execute] Re-fetched lighter execution view after start, source_doc_results:', updatedRun.source_doc_results);

    } catch (err) {
      console.error('Failed to start execution:', err);
      setError(err instanceof Error ? err.message : 'Failed to start execution');
      setIsRunning(false);
    }
  };

  const stopExecution = async () => {
    if (currentRun?.id) {
      try {
        await authenticatedFetch(`${API_BASE_URL}/runs/${currentRun.id}/cancel`, {
          method: 'POST'
        });
      } catch (err) {
        console.error('Failed to cancel run:', err);
      }
    }

    setIsRunning(false);
  };

  const pauseRun = async () => {
    if (!currentRun?.id) return;
    setIsPausing(true);
    try {
      await authenticatedFetch(`${API_BASE_URL}/runs/${currentRun.id}/pause`, {
        method: 'POST'
      });
      const updatedRun = await runsApi.getExecutionView(currentRun.id);
      setCurrentRun(updatedRun);
      setIsRunning(false);
      setRunPollingNotice(null);
    } catch (err) {
      console.error('Failed to pause run:', err);
      setError(err instanceof Error ? err.message : 'Failed to pause run');
    } finally {
      setIsPausing(false);
    }
  };

  const resumeRun = async () => {
    if (!currentRun?.id) return;
    setIsResuming(true);
    setError(null);
    try {
      await runsApi.resume(currentRun.id);
      const updatedRun = await runsApi.getExecutionView(currentRun.id);
      setCurrentRun(updatedRun);
      setIsRunning(true);
      setRunPollingNotice(null);
    } catch (err) {
      console.error('Failed to resume run:', err);
      setError(err instanceof Error ? err.message : 'Failed to resume run');
    } finally {
      setIsResuming(false);
    }
  };

  const reevaluateRun = async () => {
    if (!currentRun?.id) return;

    setIsReevaluating(true);
    setError(null);

    try {
      const response = await authenticatedFetch(`${API_BASE_URL}/runs/${currentRun.id}/reevaluate`, {
        method: 'POST'
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || 'Failed to start re-evaluation');
      }

      // Poll for updated run data after a short delay
      setTimeout(async () => {
        try {
          const updatedRun = await runsApi.getExecutionView(currentRun.id);
          setCurrentRun(updatedRun);
        } catch (err) {
          console.error('Failed to refresh run:', err);
        }
        setIsReevaluating(false);
      }, 5000); // Wait 5s before first check

    } catch (err) {
      console.error('Failed to re-evaluate:', err);
      setError(err instanceof Error ? err.message : 'Failed to re-evaluate');
      setIsReevaluating(false);
    }
  };

  const getDownloadFilename = (contentDisposition: string | null, fallback: string): string => {
    if (!contentDisposition) return fallback;
    const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match?.[1]) {
      return decodeURIComponent(utf8Match[1]);
    }
    const plainMatch = contentDisposition.match(/filename="?([^"]+)"?/i);
    return plainMatch?.[1] || fallback;
  };

  const downloadRunLog = async () => {
    if (!currentRun?.id) return;

    setIsDownloadingLog(true);
    setError(null);

    try {
      const response = await authenticatedFetch(
        `${API_BASE_URL}/runs/${currentRun.id}/logs/download?classification=all&format=txt`
      );

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        const detail = typeof data?.detail === 'string' ? data.detail : 'Failed to download run log';
        throw new Error(detail);
      }

      const blob = await response.blob();
      const fallbackName = `apicostx-log-${(currentRun.title || currentRun.id.slice(0, 8))
        .replace(/\s+/g, '_')
        .replace(/[^A-Za-z0-9_-]/g, '')
        .slice(0, 64)}.txt`;
      const filename = getDownloadFilename(response.headers.get('Content-Disposition'), fallbackName);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Failed to download run log:', err);
      setError(err instanceof Error ? err.message : 'Failed to download run log');
    } finally {
      setIsDownloadingLog(false);
    }
  };

  const runEstimate = useMemo(
    () => getRunEstimate(currentRun, fullPresetData),
    [currentRun, fullPresetData]
  );

  const phaseProgress = useMemo<PhaseProgressRow[]>(() => {
    if (!runEstimate) return [];

    const actual = getActualPhaseCounts(currentRun);

    return [
      { key: 'generation', label: 'Generation', color: '#3b82f6', actual: actual.generation, total: runEstimate.generation },
      { key: 'single-eval', label: 'Single Eval', color: '#8b5cf6', actual: actual.singleEval, total: runEstimate.single_eval },
      { key: 'pre-pairwise', label: 'Pre-Combine Pairwise', color: '#a855f7', actual: actual.preCombinePairwise, total: runEstimate.pre_combine_pairwise },
      { key: 'combine', label: 'Combine', color: '#f59e0b', actual: actual.combine, total: runEstimate.combine },
      { key: 'post-pairwise', label: 'Post-Combine Pairwise', color: '#22c55e', actual: actual.postCombinePairwise, total: runEstimate.post_combine_pairwise },
    ]
      .filter((phase) => phase.total > 0)
      .map((phase) => ({
        ...phase,
        actual: Math.min(phase.actual, phase.total),
      }));
  }, [currentRun, runEstimate]);

  const completedEstimatedCalls = phaseProgress.reduce((sum, phase) => sum + phase.actual, 0);
  const totalEstimatedCalls = phaseProgress.reduce((sum, phase) => sum + phase.total, 0);
  const hasEstimatedProgress = runEstimate !== null && totalEstimatedCalls > 0;
  const displayPhaseProgress = phaseProgress.length > 0 ? phaseProgress : EMPTY_PHASE_PROGRESS;
  const hasFreshResumeInfo = resumeInfoRunId === currentRun?.id;
  const isResumeInfoPending = Boolean(
    currentRun?.id &&
    !isRunActiveStatus(currentRun.status) &&
    !hasFreshResumeInfo
  );
  const canResumeCurrentRun = Boolean(
    currentRun &&
    !isRunActiveStatus(currentRun.status) &&
    (
      currentRun.status === 'paused' ||
      (hasFreshResumeInfo && resumeInfo?.resumable)
    )
  );
  const showReevaluateButton = Boolean(
    currentRun &&
    (currentRun.status === 'completed' || currentRun.status === 'completed_with_errors' || currentRun.status === 'failed') &&
    !canResumeCurrentRun &&
    !isResumeInfoPending
  );

  const getStatusIcon = () => {
    if (!currentRun) return <Activity size={20} />;
    switch (currentRun.status) {
      case 'running': return <Loader2 size={20} className="animate-spin" />;
      case 'completed': return <CheckCircle size={20} />;
      case 'failed': return <XCircle size={20} />;
      case 'cancelled': return <XCircle size={20} />;
      default: return <Clock size={20} />;
    }
  };

  const getStatusColor = () => {
    if (!currentRun) return '#6b7280';
    switch (currentRun.status) {
      case 'running': return '#3b82f6';
      case 'completed': return '#22c55e';
      case 'failed': return '#ef4444';
      case 'cancelled': return '#f59e0b';
      default: return '#6b7280';
    }
  };

  return (
    <div style={{ padding: '24px', backgroundColor: '#111827', minHeight: '100vh' }}>
      {/* Header */}
      <div style={{ marginBottom: '24px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <h1 style={{ fontSize: '28px', fontWeight: 'bold', color: 'white', margin: 0 }}>
              Execute Evaluation
            </h1>
            <p style={{ color: '#9ca3af', marginTop: '8px' }}>
              Run document generation and evaluation workflows
            </p>
          </div>
          <div style={{ display: 'flex', gap: '12px' }}>
            {isRunning ? (
              <button
                onClick={stopExecution}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '10px 20px',
                  backgroundColor: '#dc2626',
                  color: 'white',
                  border: 'none',
                  borderRadius: '8px',
                  cursor: 'pointer',
                  fontSize: '14px'
                }}
              >
                <Square size={18} />
                Stop Execution
              </button>
            ) : (
              <button
                onClick={startExecution}
                disabled={!selectedPreset}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '10px 20px',
                  backgroundColor: selectedPreset ? '#22c55e' : '#374151',
                  color: 'white',
                  border: 'none',
                  borderRadius: '8px',
                  cursor: selectedPreset ? 'pointer' : 'not-allowed',
                  fontSize: '14px',
                  opacity: selectedPreset ? 1 : 0.5
                }}
              >
                <Play size={18} />
                Start Execution
              </button>
            )}
            {/* Pause button - shown when run is actively running */}
            {currentRun && currentRun.status === 'running' && (
              <button
                onClick={pauseRun}
                disabled={isPausing}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '10px 20px',
                  backgroundColor: isPausing ? '#374151' : '#f97316',
                  color: 'white',
                  border: 'none',
                  borderRadius: '8px',
                  cursor: isPausing ? 'not-allowed' : 'pointer',
                  fontSize: '14px',
                  opacity: isPausing ? 0.7 : 1
                }}
              >
                <Pause size={18} />
                {isPausing ? 'Pausing...' : 'Pause'}
              </button>
            )}
            {/* Resume button - shown whenever the API service says missing work can be resumed */}
            {canResumeCurrentRun && (
              <button
                onClick={resumeRun}
                disabled={isResuming}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '10px 20px',
                  backgroundColor: isResuming ? '#374151' : '#22c55e',
                  color: 'white',
                  border: 'none',
                  borderRadius: '8px',
                  cursor: isResuming ? 'not-allowed' : 'pointer',
                  fontSize: '14px',
                  opacity: isResuming ? 0.7 : 1
                }}
              >
                <Play size={18} />
                {isResuming ? 'Resuming...' : 'Resume'}
              </button>
            )}
            {/* Re-evaluate button - shown only when resume is not available */}
            {showReevaluateButton && (
              <button
                onClick={reevaluateRun}
                disabled={isReevaluating}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '10px 20px',
                  backgroundColor: isReevaluating ? '#374151' : '#6366f1',
                  color: 'white',
                  border: 'none',
                  borderRadius: '8px',
                  cursor: isReevaluating ? 'not-allowed' : 'pointer',
                  fontSize: '14px',
                  opacity: isReevaluating ? 0.7 : 1
                }}
              >
                <RefreshCw size={18} className={isReevaluating ? 'animate-spin' : ''} />
                {isReevaluating ? 'Re-evaluating...' : 'Re-evaluate'}
              </button>
            )}
            {currentRun?.id && (
              <>
                <button
                  onClick={() => setShowLogModal(true)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '10px 20px',
                    backgroundColor: '#1d4ed8',
                    color: 'white',
                    border: 'none',
                    borderRadius: '8px',
                    cursor: 'pointer',
                    fontSize: '14px'
                  }}
                >
                  <FileText size={18} />
                  View Log
                </button>
                <button
                  onClick={downloadRunLog}
                  disabled={isDownloadingLog}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '10px 20px',
                    backgroundColor: isDownloadingLog ? '#374151' : '#0f766e',
                    color: 'white',
                    border: 'none',
                    borderRadius: '8px',
                    cursor: isDownloadingLog ? 'not-allowed' : 'pointer',
                    fontSize: '14px',
                    opacity: isDownloadingLog ? 0.75 : 1
                  }}
                >
                  <Download size={18} />
                  {isDownloadingLog ? 'Downloading...' : 'Download Log'}
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Error Alert */}
      {error && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          padding: '16px',
          backgroundColor: 'rgba(239, 68, 68, 0.1)',
          border: '1px solid rgba(239, 68, 68, 0.3)',
          borderRadius: '8px',
          marginBottom: '24px'
        }}>
          <AlertCircle size={20} style={{ color: '#ef4444' }} />
          <span style={{ color: '#fca5a5' }}>{error}</span>
        </div>
      )}

      {/* Preset Selector Card */}
      <div style={{
        backgroundColor: '#1f2937',
        borderRadius: '12px',
        padding: '20px',
        marginBottom: '24px',
        border: '1px solid #374151'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: '300px' }}>
            <label style={{ color: '#9ca3af', fontSize: '14px', marginBottom: '8px', display: 'block' }}>
              Select Preset
            </label>
            <div ref={dropdownRef} style={{ position: 'relative' }}>
              <button
                onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                style={{
                  width: '100%',
                  padding: '12px 16px',
                  backgroundColor: '#374151',
                  border: '1px solid #4b5563',
                  borderRadius: '8px',
                  color: 'white',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  cursor: 'pointer'
                }}
              >
                <span>{selectedPreset?.name || 'Select a preset...'}</span>
                <ChevronDown size={18} style={{
                  transform: isDropdownOpen ? 'rotate(180deg)' : 'none',
                  transition: 'transform 0.2s'
                }} />
              </button>
              {isDropdownOpen && (
                <div style={{
                  position: 'absolute',
                  top: '100%',
                  left: 0,
                  right: 0,
                  marginTop: '4px',
                  backgroundColor: '#374151',
                  border: '1px solid #4b5563',
                  borderRadius: '8px',
                  zIndex: 50,
                  maxHeight: '300px',
                  overflowY: 'auto'
                }}>
                  {presets.map(preset => (
                    <button
                      key={preset.id}
                      onClick={() => {
                        setSelectedPreset(preset);
                        setIsDropdownOpen(false);
                      }}
                      style={{
                        width: '100%',
                        padding: '12px 16px',
                        backgroundColor: selectedPreset?.id === preset.id ? '#4b5563' : 'transparent',
                        border: 'none',
                        color: 'white',
                        textAlign: 'left',
                        cursor: 'pointer',
                        display: 'block'
                      }}
                      onMouseEnter={(e) => {
                        if (selectedPreset?.id !== preset.id) {
                          e.currentTarget.style.backgroundColor = '#4b556380';
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (selectedPreset?.id !== preset.id) {
                          e.currentTarget.style.backgroundColor = 'transparent';
                        }
                      }}
                    >
                      <div style={{ fontWeight: 500 }}>{preset.name}</div>
                      {preset.description && (
                        <div style={{ color: '#9ca3af', fontSize: '12px', marginTop: '4px' }}>
                          {preset.description}
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {selectedPreset && (
            <>
              <div style={{ textAlign: 'center', padding: '0 20px' }}>
                <div style={{ color: '#9ca3af', fontSize: '12px' }}>Documents</div>
                <div style={{ color: 'white', fontSize: '24px', fontWeight: 'bold' }}>
                  {selectedPreset.document_count || '-'}
                </div>
              </div>
              <div style={{ textAlign: 'center', padding: '0 20px' }}>
                <div style={{ color: '#9ca3af', fontSize: '12px' }}>Models</div>
                <div style={{ color: 'white', fontSize: '24px', fontWeight: 'bold' }}>
                  {selectedPreset.model_count || '-'}
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Stats Cards */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
        gap: '16px',
        marginBottom: '24px'
      }}>
        <div style={{
          backgroundColor: '#1f2937',
          borderRadius: '12px',
          padding: '20px',
          border: '1px solid #374151'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{
              padding: '10px',
              backgroundColor: `${getStatusColor()}20`,
              borderRadius: '8px'
            }}>
              {getStatusIcon()}
            </div>
            <div>
              <div style={{ color: '#9ca3af', fontSize: '14px' }}>Status</div>
              <div style={{ color: getStatusColor(), fontSize: '18px', fontWeight: 'bold', textTransform: 'capitalize' }}>
                {currentRun?.status || 'Idle'}
              </div>
            </div>
          </div>
        </div>

        <div style={{
          backgroundColor: '#1f2937',
          borderRadius: '12px',
          padding: '20px',
          border: '1px solid #374151'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{ padding: '10px', backgroundColor: '#3b82f620', borderRadius: '8px' }}>
              <FileText size={20} style={{ color: '#3b82f6' }} />
            </div>
            <div>
              <div style={{ color: '#9ca3af', fontSize: '14px' }}>Evaluations</div>
              <div style={{ color: 'white', fontSize: '18px', fontWeight: 'bold' }}>
                {currentRun?.progress?.completed_tasks || 0} / {currentRun?.progress?.total_tasks || 0}
              </div>
            </div>
          </div>
        </div>

        <div style={{
          backgroundColor: '#1f2937',
          borderRadius: '12px',
          padding: '20px',
          border: '1px solid #374151'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{ padding: '10px', backgroundColor: '#8b5cf620', borderRadius: '8px' }}>
              <Users size={20} style={{ color: '#8b5cf6' }} />
            </div>
            <div>
              <div style={{ color: '#9ca3af', fontSize: '14px' }}>Pairwise</div>
              <div style={{ color: 'white', fontSize: '18px', fontWeight: 'bold' }}>
                {getVisiblePairwiseCount(currentRun)}
              </div>
            </div>
          </div>
        </div>

        <div style={{
          backgroundColor: '#1f2937',
          borderRadius: '12px',
          padding: '20px',
          border: '1px solid #374151'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{ padding: '10px', backgroundColor: '#22c55e20', borderRadius: '8px' }}>
              <Timer size={20} style={{ color: '#22c55e' }} />
            </div>
            <div>
              <div style={{ color: '#9ca3af', fontSize: '14px' }}>Duration</div>
              <div style={{ color: 'white', fontSize: '18px', fontWeight: 'bold' }}>
                {currentRun?.started_at
                  ? (currentRun.status === 'running' || currentRun.status === 'pending'
                      ? computeEndTime(currentRun.started_at, null, currentRun.duration_seconds)
                      : `${formatTime(currentRun.started_at)} - ${formatTime(currentRun.completed_at)}`)
                  : '--:--'}
              </div>
            </div>
          </div>
        </div>
        <div style={{
          backgroundColor: '#1f2937',
          borderRadius: '12px',
          padding: '20px',
          border: '1px solid #374151',
          gridColumn: '1 / -1'
        }}>
          <div style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            gap: '16px',
            marginBottom: '16px',
            flexWrap: 'wrap'
          }}>
            <div>
              <div style={{ color: '#9ca3af', fontSize: '14px' }}>Estimated Pipeline Progress</div>
              <div style={{ color: 'white', fontSize: '18px', fontWeight: 'bold' }}>
                {hasEstimatedProgress
                  ? `${completedEstimatedCalls} / ${totalEstimatedCalls} estimated LLM calls`
                  : 'Awaiting estimate data'}
              </div>
            </div>
            <div style={{ color: '#9ca3af', fontSize: '13px' }}>
              {hasEstimatedProgress
                ? `${runEstimate.document_count} source doc${runEstimate.document_count === 1 ? '' : 's'}`
                : 'Empty placeholder'}
            </div>
          </div>

          {!hasEstimatedProgress && (
            <div style={{ color: '#9ca3af', fontSize: '13px', marginBottom: '16px' }}>
              This section is always shown now. It will fill in after a run has saved calculator estimate data.
            </div>
          )}

          <div style={{ display: 'grid', gap: '12px' }}>
            {displayPhaseProgress.map((phase) => {
              const percent = hasEstimatedProgress && phase.total > 0
                ? Math.round((phase.actual / phase.total) * 100)
                : 0;
              return (
                <div key={phase.key}>
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '12px',
                    marginBottom: '6px',
                    flexWrap: 'wrap'
                  }}>
                    <div style={{ color: '#e5e7eb', fontSize: '14px', fontWeight: 600 }}>{phase.label}</div>
                    <div style={{ color: '#9ca3af', fontSize: '13px', fontFamily: 'monospace' }}>
                      {hasEstimatedProgress
                        ? `${phase.actual} / ${phase.total} (${percent}%)`
                        : 'No data yet'}
                    </div>
                  </div>
                  <div style={{
                    height: '10px',
                    backgroundColor: '#111827',
                    borderRadius: '999px',
                    overflow: 'hidden',
                    border: '1px solid #374151'
                  }}>
                    <div style={{
                      height: '100%',
                      width: `${percent}%`,
                      backgroundColor: phase.color,
                      borderRadius: '999px',
                      opacity: hasEstimatedProgress ? 1 : 0.35,
                      transition: 'width 0.3s ease'
                    }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* FPF Stats Card */}
        <div style={{
          backgroundColor: '#1f2937',
          borderRadius: '12px',
          padding: '20px',
          border: '1px solid #374151',
          gridColumn: '1 / -1' // Span full width
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
            <div style={{ padding: '10px', backgroundColor: '#f59e0b20', borderRadius: '8px' }}>
              <Activity size={20} style={{ color: '#f59e0b' }} />
            </div>
            <div>
              <div style={{ color: '#9ca3af', fontSize: '14px' }}>FPF Live Stats</div>
              <div style={{ color: 'white', fontSize: '18px', fontWeight: 'bold' }}>
                {runningRunsCount !== null ? `${runningRunsCount} Active Runs` : 'Checking...'}
              </div>
            </div>
          </div>

          {currentRun?.fpf_stats?.current_call && (
            <div style={{
              backgroundColor: '#111827',
              padding: '8px 12px',
              borderRadius: '6px',
              fontSize: '13px',
              color: '#d1d5db',
              borderLeft: '3px solid #3b82f6'
            }}>
              <span style={{ color: '#60a5fa', fontWeight: 'bold' }}>Running:</span> {currentRun.fpf_stats.current_call}
            </div>
          )}

          {currentRun?.fpf_stats?.last_error && (
            <div style={{
              backgroundColor: '#111827',
              padding: '8px 12px',
              borderRadius: '6px',
              fontSize: '13px',
              color: '#fca5a5',
              marginTop: '8px',
              borderLeft: '3px solid #ef4444'
            }}>
              <span style={{ color: '#f87171', fontWeight: 'bold' }}>Last Error:</span> {currentRun.fpf_stats.last_error}
            </div>
          )}

          {runPollingNotice && (
            <div style={{
              backgroundColor: '#111827',
              padding: '8px 12px',
              borderRadius: '6px',
              fontSize: '13px',
              color: '#fde68a',
              marginTop: '8px',
              borderLeft: '3px solid #f59e0b'
            }}>
              <span style={{ color: '#fbbf24', fontWeight: 'bold' }}>Polling:</span> {runPollingNotice}
            </div>
          )}
        </div>      </div>

      {currentRun?.status === 'completed_with_errors' && currentRun.error_message && (
        <div style={{
          backgroundColor: '#1f2937',
          borderRadius: '12px',
          padding: '20px',
          border: '1px solid #92400e',
          marginTop: '20px',
          marginBottom: '20px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '10px' }}>
            <div style={{ padding: '10px', backgroundColor: '#f59e0b20', borderRadius: '8px' }}>
              <AlertCircle size={20} style={{ color: '#f59e0b' }} />
            </div>
            <div>
              <div style={{ color: '#fcd34d', fontSize: '16px', fontWeight: 'bold' }}>
                Run completed with errors
              </div>
              <div style={{ color: '#fbbf24', fontSize: '13px' }}>
                Results were kept, but part of the configured work did not finish cleanly.
              </div>
            </div>
          </div>
          <div style={{
            backgroundColor: '#111827',
            padding: '12px 14px',
            borderRadius: '8px',
            fontSize: '13px',
            color: '#fde68a',
            borderLeft: '3px solid #f59e0b',
            whiteSpace: 'pre-wrap',
            lineHeight: 1.5,
          }}>
            <span style={{ color: '#fbbf24', fontWeight: 'bold' }}>Details:</span> {currentRun.error_message}
          </div>
        </div>
      )}

      {/* Results Content - Per-Source-Document Sections with Internal Tabs */}
      <div style={{
        backgroundColor: '#1f2937',
        borderRadius: '12px',
        padding: '20px',
        border: '1px solid #374151',
        minHeight: '400px'
      }}>
        {/* Multi-doc view: Show per-source-document sections */}
        {currentRun?.source_doc_results && Object.keys(currentRun.source_doc_results).length > 0 ? (
          <div>
            {/* Only show multi-doc info banner when there are 2+ documents */}
            {Object.keys(currentRun.source_doc_results).length > 1 && (
              <div style={{
                marginBottom: '16px',
                padding: '12px 16px',
                backgroundColor: '#111827',
                borderRadius: '8px',
                borderLeft: '3px solid #3b82f6',
                display: 'flex',
                alignItems: 'center',
                gap: '12px'
              }}>
                <FileText size={18} style={{ color: '#60a5fa' }} />
                <span style={{ color: '#d1d5db', fontSize: '14px' }}>
                  <strong>Multi-Document Run:</strong> Each input document runs its own independent pipeline with separate evaluations.
                </span>
                <span style={{
                  marginLeft: 'auto',
                  color: '#9ca3af',
                  fontSize: '13px'
                }}>
                  {Object.keys(currentRun.source_doc_results).length} source documents
                </span>
              </div>
            )}
            {Object.entries(currentRun.source_doc_results).map(([sourceDocId, sourceDocResult]) => (
              <SourceDocSection
                key={sourceDocId}
                sourceDocId={sourceDocId}
                sourceDocResult={sourceDocResult}
                currentRun={currentRun}
                defaultExpanded={Object.keys(currentRun.source_doc_results!).length <= 3}
                hideHeader={Object.keys(currentRun.source_doc_results!).length === 1}
              />
            ))}
          </div>
        ) : currentRun ? (
          <div style={{
            padding: '28px',
            textAlign: 'center',
            backgroundColor: '#111827',
            borderRadius: '8px',
            border: '1px solid #374151'
          }}>
            <div style={{ color: '#e5e7eb', fontSize: '20px', fontWeight: 700, marginBottom: '12px' }}>
              Source document details are not available yet
            </div>
            <div style={{ color: '#cbd5e1', fontSize: '14px', lineHeight: 1.6, maxWidth: '720px', margin: '0 auto 16px' }}>
              This run does not currently have any saved source-document detail rows. The rest of the run can still load normally, and other tabs may already have partial results.
            </div>
            <div style={{ color: '#94a3b8', fontSize: '13px' }}>
              Run status: <strong style={{ color: '#e5e7eb' }}>{currentRun.status}</strong>
            </div>
          </div>
        ) : (
          /* No run started yet - show placeholder */
          <div style={{
            padding: '40px',
            textAlign: 'center',
            color: '#9ca3af'
          }}>
            <div style={{ fontSize: '18px', marginBottom: '8px' }}>No run in progress</div>
            <div style={{ fontSize: '14px' }}>Select a preset and click "Start Execution" to begin</div>
          </div>
        )}
      </div>

      {/* Log Viewer */}
      {currentRun?.id && (
        <div style={{ marginTop: '24px' }}>
          <LogViewer
            runId={String(currentRun.id)}
            isRunning={isRunning}
            initiallyExpanded={isRunActiveStatus(currentRun.status)}
          />
        </div>
      )}

      {showLogModal && currentRun?.id && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.7)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '24px',
            zIndex: 1000,
          }}
        >
          <div
            style={{
              width: 'min(1100px, 100%)',
              height: 'min(85vh, 900px)',
              backgroundColor: '#111827',
              border: '1px solid #374151',
              borderRadius: '16px',
              overflow: 'hidden',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <div
              style={{
                padding: '16px 20px',
                borderBottom: '1px solid #374151',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '16px',
                flexWrap: 'wrap',
              }}
            >
              <div>
                <div style={{ color: 'white', fontSize: '18px', fontWeight: 'bold' }}>
                  Run Log
                </div>
                <div style={{ color: '#9ca3af', fontSize: '13px', marginTop: '4px' }}>
                  {currentRun.title || currentRun.id}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                <button
                  onClick={downloadRunLog}
                  disabled={isDownloadingLog}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '10px 16px',
                    backgroundColor: isDownloadingLog ? '#374151' : '#0f766e',
                    color: 'white',
                    border: 'none',
                    borderRadius: '8px',
                    cursor: isDownloadingLog ? 'not-allowed' : 'pointer',
                    fontSize: '14px',
                    opacity: isDownloadingLog ? 0.75 : 1,
                  }}
                >
                  <Download size={16} />
                  {isDownloadingLog ? 'Downloading...' : 'Download TXT'}
                </button>
                <button
                  onClick={() => setShowLogModal(false)}
                  style={{
                    padding: '10px 16px',
                    backgroundColor: '#374151',
                    color: 'white',
                    border: 'none',
                    borderRadius: '8px',
                    cursor: 'pointer',
                    fontSize: '14px',
                  }}
                >
                  Close
                </button>
              </div>
            </div>
            <div style={{ flex: 1, minHeight: 0, padding: '20px', overflow: 'hidden' }}>
              <LogViewer
                runId={String(currentRun.id)}
                isRunning={isRunning}
                initiallyExpanded
                allowFullscreen={false}
                title="Run Log"
                className="mt-0 flex h-full min-h-0 flex-col"
                bodyHeightClass="h-full"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
