"""core — the single FastMCP instance + shared config both tool modules use.

wiki.py registers wiki_search / wiki_get / wiki_backlinks / wiki_note_drop;
graph.py registers graph_answer / graph_search / graph_get_note / graph_entity /
graph_current_facts / graph_changes; server.py registers memory_status and
assembles the HTTP app.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    disable_created_metrics,
    generate_latest,
)
from prometheus_client.exposition import CONTENT_TYPE_LATEST

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
DIPINK_VERSION = os.environ.get("DIPINK_VERSION", "dev")
DIPINK_BUILD = (
    os.environ.get("DIPINK_BUILD")
    or os.environ.get("GIT_SHA")
    or os.environ.get("SOURCE_REVISION")
    or "unknown"
)

# Persistence dir (embedding cache + the shared query-metrics JSONL). Mount a
# volume here so restarts don't re-pay the embed cost and usage history survives.
CACHE_DIR = Path(os.environ["CACHE_DIR"]) if os.environ.get("CACHE_DIR") else None

# Query instrumentation: one JSONL for all tools (the `tool` field
# distinguishes wiki_* from graph_* events). The weekly gaps miner reads it
# back via /api/metrics. Set MCP_METRICS_PATH=off to disable.
_metrics_env = os.environ.get("MCP_METRICS_PATH", "").strip()
if _metrics_env.lower() in {"", "auto"}:
    METRICS_PATH = (CACHE_DIR / "queries.jsonl") if CACHE_DIR else None
elif _metrics_env.lower() in {"off", "none", "0", "disabled"}:
    METRICS_PATH = None
else:
    METRICS_PATH = Path(_metrics_env)
METRICS_MAX_BYTES = max(64 * 1024, int(os.environ.get("MCP_METRICS_MAX_BYTES", "5242880")))
METRICS_BACKUPS = max(0, min(int(os.environ.get("MCP_METRICS_BACKUPS", "2")), 10))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dipink")

# DNS-rebinding protection: FastMCP auto-locks to 127.0.0.1/localhost when host
# is unset. If you expose this server behind an ingress / reverse proxy, add
# its hostname via MCP_ALLOWED_HOSTS (comma-separated).
_default_hosts = "localhost,127.0.0.1,memory"
_allowed = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", _default_hosts).split(",") if h.strip()]
_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=_allowed + [f"{h}:*" for h in _allowed],
    allowed_origins=[f"https://{h}" for h in _allowed] + [f"http://{h}" for h in _allowed],
)

mcp = FastMCP("dipink-memory", stateless_http=True, host=HOST, port=PORT, transport_security=_security)


_metrics_lock = threading.Lock()

# Private registry: deterministic in tests/import reloads and no unrelated
# process metrics. Every label value is selected from a bounded enum except the
# single build version carried by dipink_info.
disable_created_metrics()
PROMETHEUS_REGISTRY = CollectorRegistry(auto_describe=True)
DIPINK_INFO = Gauge(
    "dipink_info", "dip.ink build information", ["version"], registry=PROMETHEUS_REGISTRY
)
DIPINK_INFO.labels(version=DIPINK_VERSION).set(1)
TOOL_CALLS = Counter(
    "dipink_tool_calls_total", "Memory tool calls", ["tool", "outcome"],
    registry=PROMETHEUS_REGISTRY,
)
TOOL_DURATION = Histogram(
    "dipink_tool_duration_seconds", "Memory tool duration", ["tool"],
    registry=PROMETHEUS_REGISTRY,
)
NOTE_DROP = Counter(
    "dipink_note_drop_total", "Note-drop outcomes", ["outcome"],
    registry=PROMETHEUS_REGISTRY,
)
GRAPH_ANSWER = Counter(
    "dipink_graph_answer_total", "Graph-answer outcomes",
    ["confidence", "cached", "grounded"], registry=PROMETHEUS_REGISTRY,
)
GRAPH_ANSWER_DURATION = Histogram(
    "dipink_graph_answer_duration_seconds", "Graph-answer phase duration", ["phase"],
    registry=PROMETHEUS_REGISTRY,
)

_STATE_GAUGE_NAMES = (
    "dipink_wiki_index_ready",
    "dipink_wiki_index_degraded",
    "dipink_wiki_pages_indexed",
    "dipink_wiki_index_age_seconds",
    "dipink_graph_ready",
    "dipink_inbox_notes",
    "dipink_deferred_notes",
    "dipink_blocked_notes",
    "dipink_review_queue_open",
    "dipink_ingest_pending_notes",
    "dipink_ingest_partial_notes",
    "dipink_ingest_lag_seconds",
    "dipink_community_age_seconds",
)
STATE_GAUGES = {
    name: Gauge(name, name.replace("dipink_", "").replace("_", " "), registry=PROMETHEUS_REGISTRY)
    for name in _STATE_GAUGE_NAMES
}

_KNOWN_METRIC_TOOLS = {
    "wiki_search", "wiki_get", "wiki_backlinks", "wiki_note_drop",
    "graph_answer", "graph_search", "graph_get_note", "graph_entity",
    "graph_current_facts", "graph_changes", "memory_status", "other",
}
_TOOL_ALIASES = {"search": "wiki_search", "get": "wiki_get", "backlinks": "wiki_backlinks"}
_TOOL_OUTCOMES = {"ok", "error", "not_found", "degraded"}
_NOTE_OUTCOMES = {"ok", "already_exists", "error"}
_CONFIDENCE = {"high", "medium", "low", "not_found", "error"}

# Publish zero-valued bounded series from process start so dashboards and alert
# rules never depend on a first real call to discover the schema.
for _tool in sorted(_KNOWN_METRIC_TOOLS):
    TOOL_DURATION.labels(tool=_tool)
    for _outcome in sorted(_TOOL_OUTCOMES):
        TOOL_CALLS.labels(tool=_tool, outcome=_outcome)
for _outcome in sorted(_NOTE_OUTCOMES):
    NOTE_DROP.labels(outcome=_outcome)
for _confidence in sorted(_CONFIDENCE):
    for _cached in ("false", "true"):
        for _grounded in ("false", "true", "unknown"):
            GRAPH_ANSWER.labels(
                confidence=_confidence, cached=_cached, grounded=_grounded
            )
for _phase in ("assemble", "distill"):
    GRAPH_ANSWER_DURATION.labels(phase=_phase)


def _metric_tool(event: dict) -> str:
    value = str(event.get("metric_tool") or event.get("tool") or "other")
    value = _TOOL_ALIASES.get(value, value)
    return value if value in _KNOWN_METRIC_TOOLS else "other"


def observe_tool_event(event: dict) -> None:
    """Project a query event into bounded-cardinality Prometheus metrics."""
    try:
        tool = _metric_tool(event)
        raw_outcome = str(event.get("outcome") or "")
        confidence = str(event.get("confidence") or "")
        if raw_outcome == "error" or confidence == "error" or event.get("error"):
            outcome = "error"
        elif confidence == "not_found":
            outcome = "not_found"
        elif raw_outcome == "degraded":
            outcome = "degraded"
        else:
            outcome = "ok"
        if outcome not in _TOOL_OUTCOMES:
            outcome = "error"
        TOOL_CALLS.labels(tool=tool, outcome=outcome).inc()
        duration_ms = float(event.get("duration_ms") or 0.0)
        if not duration_ms and tool == "graph_answer":
            duration_ms = float(event.get("assemble_ms") or 0.0) + float(event.get("distill_ms") or 0.0)
        TOOL_DURATION.labels(tool=tool).observe(max(0.0, duration_ms / 1000.0))

        if tool == "wiki_note_drop":
            note_outcome = str(event.get("outcome") or "ok")
            note_outcome = note_outcome if note_outcome in _NOTE_OUTCOMES else "error"
            NOTE_DROP.labels(outcome=note_outcome).inc()
        if tool == "graph_answer":
            conf = confidence if confidence in _CONFIDENCE else "error"
            cached = "true" if event.get("cached") is True else "false"
            grounded_value = event.get("grounded")
            grounded = (
                "true" if grounded_value is True
                else "false" if grounded_value is False
                else "unknown"
            )
            GRAPH_ANSWER.labels(confidence=conf, cached=cached, grounded=grounded).inc()
            GRAPH_ANSWER_DURATION.labels(phase="assemble").observe(
                max(0.0, float(event.get("assemble_ms") or 0.0) / 1000.0)
            )
            GRAPH_ANSWER_DURATION.labels(phase="distill").observe(
                max(0.0, float(event.get("distill_ms") or 0.0) / 1000.0)
            )
    except Exception as error:  # instrumentation must never break a tool
        log.warning("failed to update Prometheus tool metrics: %s", type(error).__name__)


def update_state_metrics(snapshot: dict) -> None:
    components = snapshot.get("components") or {}
    index = snapshot.get("index") or {}
    queues = snapshot.get("queues") or {}
    ingest = snapshot.get("ingest") or {}
    communities = snapshot.get("communities") or {}

    wiki_ready = bool((components.get("wiki") or {}).get("ready"))
    graph_ready = bool((components.get("graph") or {}).get("ready"))
    index_age = index.get("age_seconds")
    community_age = communities.get("age_seconds")
    community_count = int(communities.get("count") or 0)
    values = {
        "dipink_wiki_index_ready": 1 if wiki_ready else 0,
        "dipink_wiki_index_degraded": 1 if index.get("degraded") else 0,
        "dipink_wiki_pages_indexed": float(index.get("pages_indexed") or 0),
        "dipink_wiki_index_age_seconds": (
            float(index_age) if index_age is not None else (float("inf") if not wiki_ready else 0.0)
        ),
        "dipink_graph_ready": 1 if graph_ready else 0,
        "dipink_inbox_notes": float((queues.get("inbox") or {}).get("count") or 0),
        "dipink_deferred_notes": float((queues.get("deferred") or {}).get("count") or 0),
        "dipink_blocked_notes": float((queues.get("blocked") or {}).get("count") or 0),
        "dipink_review_queue_open": float(queues.get("review_queue_open") or 0),
        "dipink_ingest_pending_notes": float(ingest.get("pending") or 0),
        "dipink_ingest_partial_notes": float(ingest.get("partial") or 0),
        "dipink_ingest_lag_seconds": float(ingest.get("lag_seconds") or 0.0),
        "dipink_community_age_seconds": (
            float(community_age) if community_age is not None
            else (float("inf") if graph_ready and community_count == 0 else 0.0)
        ),
    }
    for name, value in values.items():
        STATE_GAUGES[name].set(value)


def render_prometheus(snapshot: dict) -> bytes:
    update_state_metrics(snapshot)
    return generate_latest(PROMETHEUS_REGISTRY)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _rotated_metrics_path(index: int) -> Path:
    assert METRICS_PATH is not None
    return Path(f"{METRICS_PATH}.{index}")


def _rotate_metrics_locked(incoming_bytes: int) -> None:
    if METRICS_PATH is None or not METRICS_PATH.exists():
        return
    try:
        if METRICS_PATH.stat().st_size + incoming_bytes <= METRICS_MAX_BYTES:
            return
        if METRICS_BACKUPS == 0:
            METRICS_PATH.unlink(missing_ok=True)
            return
        _rotated_metrics_path(METRICS_BACKUPS).unlink(missing_ok=True)
        for index in range(METRICS_BACKUPS - 1, 0, -1):
            source = _rotated_metrics_path(index)
            if source.exists():
                source.replace(_rotated_metrics_path(index + 1))
        METRICS_PATH.replace(_rotated_metrics_path(1))
    except OSError as error:
        log.warning("failed to rotate query metrics at %s: %s", METRICS_PATH, error)


def record_query(event: dict) -> None:
    """Record a tool-call event: always to stdout (visible in container logs),
    and to METRICS_PATH as JSONL when configured. Best-effort — instrumentation
    must never break a query."""
    try:
        payload = json.dumps(event, ensure_ascii=False, default=str)
    except Exception:
        return
    log.info("query %s", payload)
    observe_tool_event(event)
    if METRICS_PATH is None:
        return
    try:
        with _metrics_lock:
            METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
            encoded_size = len((payload + "\n").encode("utf-8"))
            _rotate_metrics_locked(encoded_size)
            with METRICS_PATH.open("a", encoding="utf-8") as fh:
                fh.write(payload + "\n")
    except Exception as e:
        log.warning("failed to record query metric to %s: %s", METRICS_PATH, e)


def read_metrics(days: float) -> list[dict]:
    """Read bounded rotated query logs, filtered by timestamp (max 5000)."""
    days = max(0.0, min(float(days), 365.0))
    cutoff = time.time() - days * 86400
    events: list[dict] = []
    if METRICS_PATH is None:
        return events
    # Oldest backup first, then the active file, preserving event order.
    paths = [
        _rotated_metrics_path(index)
        for index in range(METRICS_BACKUPS, 0, -1)
    ] + [METRICS_PATH]
    with _metrics_lock:
        for path in paths:
            if not path.exists():
                continue
            try:
                with path.open(encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            event = json.loads(line)
                            if event.get("ts", 0) >= cutoff:
                                events.append(event)
                        except Exception:
                            continue
            except OSError as error:
                log.warning("failed to read query metrics from %s: %s", path, error)
    return events[-5000:]
