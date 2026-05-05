"""
Internal dashboard summary routes.

These endpoints are read-only and private-network-only in the self-hosted app.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from ipaddress import ip_address
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status

from app.auth.middleware import get_recent_local_user_count
from app.config import get_settings

router = APIRouter(prefix="/internal", tags=["internal"])

_PROCESS_STARTED_AT = datetime.now(timezone.utc)
_REQUEST_EVENT_WINDOW_SECONDS = 3600
_SUMMARY_EXCLUDED_ROUTES = {"/api/internal/dashboard-summary"}
_REQUEST_EVENTS: deque[Dict[str, Any]] = deque()
_REQUEST_EVENTS_LOCK = threading.Lock()
_REQUESTS_IN_PROGRESS = 0
_REQUESTS_IN_PROGRESS_LOCK = threading.Lock()
_SECURITY_SUMMARY_PATH = Path("/var/lib/prometheus/node-exporter/apicostx-security-summary.prom")
_PROM_LINE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>[-+0-9.eE]+)\s*$"
)
_PROM_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


def _require_private_network(request: Request) -> None:
    client = request.client.host if request.client else ""
    try:
        address = ip_address(client)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Internal dashboard API is only available on the private network",
        ) from exc

    if not (address.is_loopback or address.is_private):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Internal dashboard API is only available on the private network",
        )



def increment_requests_in_progress() -> None:
    global _REQUESTS_IN_PROGRESS
    with _REQUESTS_IN_PROGRESS_LOCK:
        _REQUESTS_IN_PROGRESS += 1


def decrement_requests_in_progress() -> None:
    global _REQUESTS_IN_PROGRESS
    with _REQUESTS_IN_PROGRESS_LOCK:
        _REQUESTS_IN_PROGRESS = max(_REQUESTS_IN_PROGRESS - 1, 0)


def _get_requests_in_progress() -> int:
    with _REQUESTS_IN_PROGRESS_LOCK:
        return _REQUESTS_IN_PROGRESS


def _prune_request_events(now_seconds: float) -> None:
    cutoff = now_seconds - _REQUEST_EVENT_WINDOW_SECONDS
    while _REQUEST_EVENTS and _REQUEST_EVENTS[0]["ts"] < cutoff:
        _REQUEST_EVENTS.popleft()


def record_request_summary_event(
    *,
    route: str,
    status_code: int,
    status_class: str,
    elapsed_seconds: float,
) -> None:
    if not route.startswith("/api/"):
        return
    if route in _SUMMARY_EXCLUDED_ROUTES:
        return

    now_seconds = time.time()
    event = {
        "ts": now_seconds,
        "route": route,
        "status_code": int(status_code),
        "status_class": status_class,
        "elapsed_ms": float(elapsed_seconds) * 1000.0,
    }
    with _REQUEST_EVENTS_LOCK:
        _prune_request_events(now_seconds)
        _REQUEST_EVENTS.append(event)


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])

    position = (len(sorted_values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return float(sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction)


def _top_n_route_items(route_values: Dict[str, Dict[str, float]], sort_key: str, limit: int = 10) -> list[Dict[str, float | str]]:
    rows = []
    for route, metrics in route_values.items():
        rows.append(
            {
                "route": route,
                "requests": int(metrics.get("requests", 0)),
                "errors": int(metrics.get("errors", 0)),
                "error_rate": float(metrics.get("error_rate", 0.0)),
                "latency_p95_ms": float(metrics.get("latency_p95_ms", 0.0)),
            }
        )
    rows.sort(key=lambda row: (row.get(sort_key, 0), row.get("requests", 0)), reverse=True)
    return rows[:limit]


def get_request_summary_snapshot() -> Dict[str, Any]:
    now_seconds = time.time()
    with _REQUEST_EVENTS_LOCK:
        _prune_request_events(now_seconds)
        events = list(_REQUEST_EVENTS)

    events_5m = [event for event in events if event["ts"] >= now_seconds - 300]
    events_15m = [event for event in events if event["ts"] >= now_seconds - 900]
    events_1h = events

    latencies_5m = sorted(event["elapsed_ms"] for event in events_5m)
    status_counts_5m: Counter[str] = Counter(event["status_class"] for event in events_5m)

    route_requests_5m: Counter[str] = Counter()
    route_errors_1h: Counter[str] = Counter()
    route_request_totals_1h: Counter[str] = Counter()
    route_latencies_5m: defaultdict[str, list[float]] = defaultdict(list)

    for event in events_5m:
        route_requests_5m[event["route"]] += 1
        route_latencies_5m[event["route"]].append(event["elapsed_ms"])

    for event in events_1h:
        route_request_totals_1h[event["route"]] += 1
        if int(event["status_code"]) >= 400:
            route_errors_1h[event["route"]] += 1

    route_request_rows: Dict[str, Dict[str, float]] = {}
    for route, request_count in route_requests_5m.items():
        route_request_rows[route] = {
            "requests": float(request_count),
            "errors": float(route_errors_1h.get(route, 0)),
            "error_rate": float(route_errors_1h.get(route, 0)) / float(route_request_totals_1h.get(route, 1)),
            "latency_p95_ms": _percentile(sorted(route_latencies_5m.get(route, [])), 0.95),
        }

    route_error_rows: Dict[str, Dict[str, float]] = {}
    for route, error_count in route_errors_1h.items():
        route_error_rows[route] = {
            "requests": float(route_request_totals_1h.get(route, 0)),
            "errors": float(error_count),
            "error_rate": float(error_count) / float(route_request_totals_1h.get(route, 1)),
            "latency_p95_ms": _percentile(sorted(route_latencies_5m.get(route, [])), 0.95),
        }

    route_latency_rows: Dict[str, Dict[str, float]] = {}
    for route, latencies in route_latencies_5m.items():
        route_latency_rows[route] = {
            "requests": float(route_requests_5m.get(route, 0)),
            "errors": float(route_errors_1h.get(route, 0)),
            "error_rate": float(route_errors_1h.get(route, 0)) / float(route_request_totals_1h.get(route, 1)),
            "latency_p95_ms": _percentile(sorted(latencies), 0.95),
        }

    return {
        "requests_per_minute_5m": float(len(events_5m)) / 5.0,
        "requests_total_15m": len(events_15m),
        "requests_total_1h": len(events_1h),
        "requests_in_progress": _get_requests_in_progress(),
        "latency_p50_ms_5m": _percentile(latencies_5m, 0.50),
        "latency_p95_ms_5m": _percentile(latencies_5m, 0.95),
        "http_5xx_per_minute_5m": float(sum(1 for event in events_5m if event["status_class"] == "5xx")) / 5.0,
        "http_5xx_count_15m": sum(1 for event in events_15m if event["status_class"] == "5xx"),
        "http_5xx_count_1h": sum(1 for event in events_1h if event["status_class"] == "5xx"),
        "http_4xx_per_minute_5m": float(sum(1 for event in events_5m if event["status_class"] == "4xx")) / 5.0,
        "http_4xx_count_15m": sum(1 for event in events_15m if event["status_class"] == "4xx"),
        "http_4xx_count_1h": sum(1 for event in events_1h if event["status_class"] == "4xx"),
        "status_class_mix_5m": {
            "2xx": int(status_counts_5m.get("2xx", 0)),
            "3xx": int(status_counts_5m.get("3xx", 0)),
            "4xx": int(status_counts_5m.get("4xx", 0)),
            "5xx": int(status_counts_5m.get("5xx", 0)),
        },
        "top_routes_by_requests_5m": _top_n_route_items(route_request_rows, "requests"),
        "top_routes_by_errors_1h": _top_n_route_items(route_error_rows, "errors"),
        "top_routes_by_latency_p95_5m": _top_n_route_items(route_latency_rows, "latency_p95_ms"),
    }


def _parse_prometheus_labels(raw_labels: str | None) -> Dict[str, str]:
    if not raw_labels:
        return {}
    labels: Dict[str, str] = {}
    for key, value in _PROM_LABEL_RE.findall(raw_labels):
        labels[key] = bytes(value, "utf-8").decode("unicode_escape")
    return labels


def _parse_security_summary_metrics() -> Dict[str, Any]:
    fail2ban_status: Dict[str, Dict[str, int]] = defaultdict(dict)
    fail2ban_actions_recent: Dict[str, Dict[str, Dict[str, int]]] = {
        "15m": defaultdict(dict),
        "1h": defaultdict(dict),
        "24h": defaultdict(dict),
    }
    fail2ban_last_action: Dict[str, Dict[str, str]] = defaultdict(dict)

    if not _SECURITY_SUMMARY_PATH.exists():
        return {
            "fail2ban_status": {},
            "fail2ban_actions_recent": {window: {} for window in fail2ban_actions_recent},
            "fail2ban_last_action": {},
        }

    for line in _SECURITY_SUMMARY_PATH.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        match = _PROM_LINE_RE.match(line)
        if not match:
            continue

        metric_name = match.group("name")
        labels = _parse_prometheus_labels(match.group("labels"))
        value = float(match.group("value"))

        if metric_name == "apicostx_fail2ban_status_value":
            jail = labels.get("jail")
            metric = labels.get("metric")
            if jail and metric:
                fail2ban_status[jail][metric] = int(value)
        elif metric_name == "apicostx_fail2ban_actions_recent_total":
            window = labels.get("window")
            jail = labels.get("jail")
            action = labels.get("action")
            if window in fail2ban_actions_recent and jail and action:
                fail2ban_actions_recent[window][jail][action] = int(value)
        elif metric_name == "apicostx_fail2ban_last_action_timestamp_seconds":
            jail = labels.get("jail")
            action = labels.get("action")
            if jail and action and value > 0:
                fail2ban_last_action[jail][action] = datetime.fromtimestamp(value, tz=timezone.utc).isoformat()

    return {
        "fail2ban_status": {jail: dict(metrics) for jail, metrics in fail2ban_status.items()},
        "fail2ban_actions_recent": {
            window: {jail: dict(actions) for jail, actions in jail_map.items()}
            for window, jail_map in fail2ban_actions_recent.items()
        },
        "fail2ban_last_action": {jail: dict(actions) for jail, actions in fail2ban_last_action.items()},
    }


def _get_host_summary() -> Dict[str, Any]:
    try:
        filesystem = os.statvfs("/")
        root_disk_free_bytes = int(filesystem.f_bavail * filesystem.f_frsize)
        root_disk_total_bytes = int(filesystem.f_blocks * filesystem.f_frsize)
    except OSError:
        root_disk_free_bytes = 0
        root_disk_total_bytes = 0

    root_disk_free_percent = (
        (float(root_disk_free_bytes) / float(root_disk_total_bytes)) * 100.0
        if root_disk_total_bytes > 0
        else 0.0
    )

    try:
        load1 = float(os.getloadavg()[0])
    except (AttributeError, OSError):
        load1 = 0.0

    cpu_count = int(os.cpu_count() or 1)
    load1_per_cpu = load1 / float(cpu_count) if cpu_count > 0 else 0.0

    return {
        "root_disk_free_bytes": root_disk_free_bytes,
        "root_disk_total_bytes": root_disk_total_bytes,
        "root_disk_free_percent": root_disk_free_percent,
        "load1": load1,
        "cpu_count": cpu_count,
        "load1_per_cpu": load1_per_cpu,
    }


@router.get("/dashboard-summary")
async def get_internal_dashboard_summary(request: Request):
    _require_private_network(request)

    security_summary = _parse_security_summary_metrics()
    request_summary = get_request_summary_snapshot()
    host_summary = _get_host_summary()

    try:
        from app.api.routes.runs.execution import _active_executors
        active_runs = len(_active_executors)
    except Exception:
        active_runs = -1

    now = datetime.now(timezone.utc)
    uptime_seconds = max((now - _PROCESS_STARTED_AT).total_seconds(), 0.0)
    current_api_users_5m = get_recent_local_user_count()
    safe_to_restart = active_runs == 0

    return {
        "generated_at": now.isoformat(),
        "runs": {
            "active_runs": active_runs,
            "current_api_users_5m": current_api_users_5m,
            "safe_to_restart": safe_to_restart,
        },
        "health": {
            "origin_up": True,
            "origin_safe_to_restart": safe_to_restart,
        },
        "traffic": {
            "requests_per_minute_5m": request_summary["requests_per_minute_5m"],
            "requests_total_15m": request_summary["requests_total_15m"],
            "requests_total_1h": request_summary["requests_total_1h"],
            "requests_in_progress": request_summary["requests_in_progress"],
            "current_api_users_5m": current_api_users_5m,
            "latency_p50_ms_5m": request_summary["latency_p50_ms_5m"],
            "latency_p95_ms_5m": request_summary["latency_p95_ms_5m"],
        },
        "errors": {
            "http_5xx_per_minute_5m": request_summary["http_5xx_per_minute_5m"],
            "http_5xx_count_15m": request_summary["http_5xx_count_15m"],
            "http_5xx_count_1h": request_summary["http_5xx_count_1h"],
            "http_4xx_per_minute_5m": request_summary["http_4xx_per_minute_5m"],
            "http_4xx_count_15m": request_summary["http_4xx_count_15m"],
            "http_4xx_count_1h": request_summary["http_4xx_count_1h"],
            "status_class_mix_5m": request_summary["status_class_mix_5m"],
        },
        "routes": {
            "top_routes_by_requests_5m": request_summary["top_routes_by_requests_5m"],
            "top_routes_by_errors_1h": request_summary["top_routes_by_errors_1h"],
            "top_routes_by_latency_p95_5m": request_summary["top_routes_by_latency_p95_5m"],
        },
        "security": {
            "fail2ban_status": security_summary["fail2ban_status"],
            "fail2ban_actions_recent": security_summary["fail2ban_actions_recent"],
            "fail2ban_last_action_timestamps": security_summary["fail2ban_last_action"],
        },
        "restart": {
            "api_restarted_recently": uptime_seconds < 600,
            "api_last_started_at": _PROCESS_STARTED_AT.isoformat(),
            "uptime_seconds": uptime_seconds,
        },
        "host": host_summary,
    }
