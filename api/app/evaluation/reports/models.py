from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any

class TimelinePhase(str, Enum):
    PRECOMBINE_SINGLE = "precombine-single-eval"
    PRECOMBINE_PAIRWISE = "precombine-pairwise-eval"
    COMBINER_GENERATION = "combiner-generation"
    POSTCOMBINE_SINGLE = "postcombine-single-eval"
    POSTCOMBINE_PAIRWISE = "postcombine-pairwise-eval"
    GENERATION = "generation"  # Added for completeness

class TimelineStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    EMPTY = "empty"
    MISSING = "missing"
    PENDING = "pending"
    RUNNING = "running"

@dataclass
class TimelineRow:
    """A single row in the evaluation timeline chart."""
    expected_run_index: int
    phase: TimelinePhase
    eval_type: str  # single, pairwise, generation
    judge_model: str
    target: str

    # Actuals
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    tokens: Optional[int] = None
    status: TimelineStatus = TimelineStatus.MISSING
    source_used: str = "config"  # logs, sqlite, csv, config

    # Context
    run_id: Optional[str] = None
    actual_target: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expected_run_index": self.expected_run_index,
            "phase": self.phase.value,
            "eval_type": self.eval_type,
            "judge_model": self.judge_model,
            "target": self.target,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": self.duration_seconds,
            "tokens": self.tokens,
            "status": self.status.value,
            "source_used": self.source_used,
            "run_id": self.run_id,
            "actual_target": self.actual_target,
        }

@dataclass
class TimelinePhaseSummary:
    phase: TimelinePhase
    count: int = 0
    total_duration: float = 0.0
    total_tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase.value,
            "count": self.count,
            "total_duration": self.total_duration,
            "total_tokens": self.total_tokens,
        }

@dataclass
class TimelineChart:
    rows: List[TimelineRow] = field(default_factory=list)
    summaries: Dict[str, TimelinePhaseSummary] = field(default_factory=dict)
    total_tokens: int = 0
    total_duration: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rows": [r.to_dict() for r in self.rows],
            "summaries": {k: v.to_dict() for k, v in self.summaries.items()},
            "total_tokens": self.total_tokens,
            "total_duration": self.total_duration,
        }
