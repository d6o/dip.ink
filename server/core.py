"""core — the single FastMCP instance + shared config both tool modules use.

wiki.py registers wiki_search / wiki_get / wiki_backlinks / wiki_note_drop;
graph.py registers graph_answer / graph_search / graph_get_note / graph_entity /
graph_current_facts / graph_changes. server.py assembles the HTTP app and runs.
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

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))

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


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def record_query(event: dict) -> None:
    """Record a tool-call event: always to stdout (visible in container logs),
    and to METRICS_PATH as JSONL when configured. Best-effort — instrumentation
    must never break a query."""
    try:
        payload = json.dumps(event, ensure_ascii=False, default=str)
    except Exception:
        return
    log.info("query %s", payload)
    if METRICS_PATH is None:
        return
    try:
        with _metrics_lock:
            METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with METRICS_PATH.open("a", encoding="utf-8") as fh:
                fh.write(payload + "\n")
    except Exception as e:
        log.warning("failed to record query metric to %s: %s", METRICS_PATH, e)


def read_metrics(days: float) -> list[dict]:
    """Tail of the shared query log, filtered by event ts. At most 5000 events."""
    cutoff = time.time() - days * 86400
    events: list[dict] = []
    if METRICS_PATH is not None and METRICS_PATH.exists():
        with METRICS_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                    if e.get("ts", 0) >= cutoff:
                        events.append(e)
                except Exception:
                    continue
    return events[-5000:]
