"""dip.ink memory server — ONE MCP server for the whole memory.

Assembles the two tool modules onto a single FastMCP instance + HTTP app:

  wiki.py   wiki_search / wiki_get / wiki_backlinks / wiki_note_drop
            (+ /api/search, /api/page, /api/backlinks, /api/reindex, /live)
  graph.py  graph_answer / graph_search / graph_get_note / graph_entity /
            graph_current_facts / graph_changes
            (+ /api/answer, /api/graph/search, /api/graph/health)

Shared here: /mcp (the single MCP transport), /health (combined readiness),
/api/metrics (the one query-instrumentation log both sides write to).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import copy
from datetime import datetime, timezone
import functools
import os
from pathlib import Path
import re
import threading
import time
from typing import Any

import anyio.to_thread
import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

import core
import graph
import wiki


STATUS_SLUG_TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})-")
STATUS_CACHE_TTL = max(0.0, float(os.environ.get("MEMORY_STATUS_CACHE_TTL", "15")))
STATUS_BLOCKED_LIMIT = max(1, min(int(os.environ.get("MEMORY_STATUS_BLOCKED_LIMIT", "20")), 100))
DIPINK_VERSION = os.environ.get("DIPINK_VERSION", "dev")
DIPINK_BUILD = (
    os.environ.get("DIPINK_BUILD")
    or os.environ.get("GIT_SHA")
    or os.environ.get("SOURCE_REVISION")
    or "unknown"
)

_STATUS_CACHE: tuple[float, dict] | None = None
_STATUS_CACHE_LOCK = threading.Lock()


def _safe_error(error: BaseException) -> str:
    return type(error).__name__


def _to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if hasattr(value, "to_native"):
        value = value.to_native()
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _age_seconds(value: datetime | None, now: datetime) -> float | None:
    if value is None:
        return None
    return max(0.0, (now - value).total_seconds())


def _slug_timestamp(name: str) -> datetime | None:
    if name.endswith(".md"):
        name = name[:-3]
    match = STATUS_SLUG_TS_RE.match(name)
    if not match:
        return None
    try:
        return datetime(*(int(part) for part in match.groups()), tzinfo=timezone.utc)
    except ValueError:
        return None


def _queue_entries(path: Path) -> list[tuple[str, datetime | None, Path]]:
    if not path.is_dir():
        return []
    entries: list[tuple[str, datetime | None, Path]] = []
    for item in path.iterdir():
        if item.name.startswith(".") or item.name == "README.md":
            continue
        slug = item.stem if item.is_file() else item.name
        ts = _slug_timestamp(slug)
        if ts is None:
            try:
                ts = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
            except OSError:
                ts = None
        entries.append((slug, ts, item))
    return entries


def _blocked_reason(folder: Path) -> str:
    marker = folder / "BLOCKED.md" if folder.is_dir() else folder
    if not marker.is_file():
        return "unspecified"
    try:
        content = marker.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unreadable"
    match = re.match(r"^---\n(.*?)\n---\n?", content, re.DOTALL)
    if match:
        try:
            frontmatter = yaml.safe_load(match.group(1)) or {}
            for key in ("reason", "blocked-reason", "blocked_reason"):
                value = frontmatter.get(key)
                if value:
                    return " ".join(str(value).split())[:160]
        except yaml.YAMLError:
            pass
    for line in content.splitlines():
        reason_match = re.match(r"\s*(?:reason|blocked reason)\s*:\s*(.+)", line, re.I)
        if reason_match:
            return " ".join(reason_match.group(1).split())[:160]
    return "unspecified"


def _review_queue_open(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    queue = content.split("## Queue", 1)[1] if "## Queue" in content else content
    return sum(
        1 for line in queue.splitlines()
        if re.match(r"^\s*[-*]\s+(?!\(empty\b)", line, re.I)
    )


def _collect_repo_status() -> dict:
    root = Path(wiki.WIKI_ROOT)
    now = datetime.now(timezone.utc)
    notes_root = root / "notes"
    inbox = _queue_entries(notes_root)
    deferred = _queue_entries(notes_root / ".deferred")
    blocked = _queue_entries(notes_root / ".blocked")

    blocked_sorted = sorted(blocked, key=lambda item: item[1] or datetime.max.replace(tzinfo=timezone.utc))
    blocked_items = [{
        "slug": slug,
        "reason": _blocked_reason(path),
        "age_seconds": _age_seconds(ts, now),
    } for slug, ts, path in blocked_sorted[:STATUS_BLOCKED_LIMIT]]

    all_notes = list(inbox) + list(deferred) + list(blocked)
    archive = root / "wiki" / "sources" / "notes"
    if archive.is_dir():
        for folder in archive.glob("*/*/*/*"):
            if folder.is_dir():
                all_notes.append((folder.name, _slug_timestamp(folder.name), folder))
    newest = max(
        ((ts, slug) for slug, ts, _path in all_notes if ts is not None),
        default=None,
    )

    def queue_summary(entries: list[tuple[str, datetime | None, Path]]) -> dict:
        timestamps = [ts for _slug, ts, _path in entries if ts is not None]
        oldest = min(timestamps, default=None)
        return {
            "count": len(entries),
            "oldest_age_seconds": _age_seconds(oldest, now),
        }

    clone_required = bool(wiki.WIKI_REPO_URL)
    git_ready = root.is_dir() and ((root / ".git").exists() if clone_required else True)
    return {
        "component": {
            "ready": git_ready,
            "mode": "clone" if clone_required else "static",
            "error": None if git_ready else "repository_unavailable",
        },
        "queues": {
            "inbox": queue_summary(inbox),
            "deferred": queue_summary(deferred),
            "blocked": {
                **queue_summary(blocked),
                "items": blocked_items,
                "items_truncated": len(blocked) > len(blocked_items),
            },
            "review_queue_open": _review_queue_open(root / "wiki" / "Curator review queue.md"),
        },
        "newest_note": {
            "slug": newest[1],
            "at": newest[0].isoformat(),
            "age_seconds": _age_seconds(newest[0], now),
        } if newest else None,
    }


KNOWN_USAGE_TOOLS = {
    "search", "get", "backlinks",  # legacy wiki event names
    "wiki_search", "wiki_get", "wiki_backlinks", "wiki_note_drop",
    "graph_answer", "graph_search", "graph_get_note", "graph_entity",
    "graph_current_facts", "graph_changes", "memory_status",
}


def _collect_usage_status() -> dict:
    events = [event for event in core.read_metrics(1) if not event.get("test")]
    by_tool: dict[str, int] = {}
    errors = 0
    cache_hits = 0
    confidence = {name: 0 for name in ("high", "medium", "low", "not_found", "error")}
    for event in events:
        tool = str(event.get("tool") or "other")
        tool = tool if tool in KNOWN_USAGE_TOOLS else "other"
        by_tool[tool] = by_tool.get(tool, 0) + 1
        conf = str(event.get("confidence") or "")
        if conf in confidence:
            confidence[conf] += 1
        if event.get("cached") is True:
            cache_hits += 1
        if conf == "error" or event.get("outcome") == "error" or bool(event.get("error")):
            errors += 1
    return {
        "total": len(events),
        "errors": errors,
        "cache_hits": cache_hits,
        "by_tool": dict(sorted(by_tool.items())),
        "graph_answer_confidence": confidence,
    }


def _wiki_status() -> tuple[dict, dict]:
    snapshot = wiki.idx.snapshot()
    now = time.time()
    last_success = float(snapshot.get("last_success_unix") or 0.0)
    component = {
        "ready": bool(snapshot.get("ready")),
        "degraded": bool(snapshot.get("degraded")),
        "error": snapshot.get("last_error"),
    }
    index = {
        "pages_indexed": int(snapshot.get("pages_indexed") or 0),
        "pages_cataloged": int(snapshot.get("pages_cataloged") or 0),
        "pages_scanned": int(snapshot.get("pages_scanned") or 0),
        "pages_omitted": int(snapshot.get("pages_omitted") or 0),
        "age_seconds": max(0.0, now - last_success) if last_success else None,
        "degraded": bool(snapshot.get("degraded")),
        "status": snapshot.get("status"),
    }
    return component, index


async def _graph_status() -> tuple[dict, dict, dict]:
    group_id = os.environ.get("GROUP_ID", "main")
    component = {"ready": False, "error": None}
    ingest = {
        "total": 0,
        "done": 0,
        "pending": 0,
        "partial": 0,
        "changed": 0,
        "lag_seconds": 0.0,
        "oldest_pending_at": None,
        "newest_episode": None,
        "watermark": None,
        "error": None,
    }
    communities = {"count": 0, "age_seconds": None, "newest_at": None, "error": None}
    try:
        import graph

        client = await graph._get_graph()
        await client.driver.health_check()
        component["ready"] = True
    except Exception as error:  # noqa: BLE001
        component["error"] = _safe_error(error)
        ingest["error"] = "graph_unavailable"
        communities["error"] = "graph_unavailable"
        return component, ingest, communities

    try:
        from ingest import collect_ingest_status, discover_notes, note_content_hash
        repo_root = Path(wiki.WIKI_ROOT)
        notes = await anyio.to_thread.run_sync(functools.partial(
            discover_notes,
            [repo_root / "wiki" / "sources" / "notes", repo_root / "notes"],
        ))
        hashes = await anyio.to_thread.run_sync(
            lambda: {slug: note_content_hash(path) for _ts, slug, path in notes}
        )
        state = await collect_ingest_status(
            client,
            notes,
            group_id=group_id,
            upgrade_legacy=True,
            precomputed_hashes=hashes,
        )
        ingest.update({
            "total": state["total"],
            "done": state["done"],
            "pending": state["pending"],
            "partial": state["partial"],
            "changed": state["changed"],
            "lag_seconds": state["lag_seconds"],
            "oldest_pending_at": state["oldest_pending_at"],
            "newest_episode": state["newest_ingested_episode"],
            "watermark": state["ingest_watermark"],
        })
    except Exception as error:  # noqa: BLE001
        ingest["error"] = _safe_error(error)

    try:
        rows, _, _ = await client.driver.execute_query(
            "MATCH (c:Community {group_id: $group_id}) "
            "RETURN count(c) AS count, max(c.created_at) AS newest",
            group_id=group_id,
            routing_="r",
        )
        row = rows[0] if rows else {}
        newest = _to_datetime(row.get("newest"))
        communities.update({
            "count": int(row.get("count") or 0),
            "age_seconds": _age_seconds(newest, datetime.now(timezone.utc)),
            "newest_at": newest.isoformat() if newest else None,
        })
    except Exception as error:  # noqa: BLE001
        communities["error"] = _safe_error(error)
    return component, ingest, communities


async def collect_status() -> dict:
    """Collect each component independently; one failure never hides the rest."""
    try:
        wiki_component, index = _wiki_status()
    except Exception as error:  # noqa: BLE001
        wiki_component = {"ready": False, "degraded": False, "error": _safe_error(error)}
        index = {
            "pages_indexed": 0,
            "pages_cataloged": 0,
            "pages_scanned": 0,
            "pages_omitted": 0,
            "age_seconds": None,
            "degraded": False,
            "status": "error",
        }

    try:
        repo = await anyio.to_thread.run_sync(_collect_repo_status)
    except Exception as error:  # noqa: BLE001
        repo = {
            "component": {"ready": False, "mode": "unknown", "error": _safe_error(error)},
            "queues": {
                "inbox": {"count": 0, "oldest_age_seconds": None},
                "deferred": {"count": 0, "oldest_age_seconds": None},
                "blocked": {
                    "count": 0,
                    "oldest_age_seconds": None,
                    "items": [],
                    "items_truncated": False,
                },
                "review_queue_open": 0,
            },
            "newest_note": None,
        }

    graph_component, ingest, communities = await _graph_status()
    try:
        usage = await anyio.to_thread.run_sync(_collect_usage_status)
    except Exception as error:  # noqa: BLE001
        usage = {
            "total": 0,
            "errors": 0,
            "cache_hits": 0,
            "by_tool": {},
            "graph_answer_confidence": {
                name: 0 for name in ("high", "medium", "low", "not_found", "error")
            },
            "error": _safe_error(error),
        }

    components = {
        "wiki": wiki_component,
        "graph": graph_component,
        "git_clone": repo["component"],
    }
    return {
        "ok": all(component.get("ready") for component in components.values()),
        "generated_at": core.now_iso(),
        "components": components,
        "index": index,
        "queues": repo["queues"],
        "notes": {"newest": repo["newest_note"]},
        "ingest": ingest,
        "communities": communities,
        "usage_24h": usage,
        "build": {"version": DIPINK_VERSION, "revision": DIPINK_BUILD},
    }


async def get_status(*, force: bool = False) -> dict:
    global _STATUS_CACHE
    now = time.monotonic()
    with _STATUS_CACHE_LOCK:
        if (
            not force
            and _STATUS_CACHE is not None
            and now - _STATUS_CACHE[0] < STATUS_CACHE_TTL
        ):
            return copy.deepcopy(_STATUS_CACHE[1])
    snapshot = await collect_status()
    with _STATUS_CACHE_LOCK:
        _STATUS_CACHE = (time.monotonic(), copy.deepcopy(snapshot))
    return snapshot


def invalidate_status_cache() -> None:
    global _STATUS_CACHE
    with _STATUS_CACHE_LOCK:
        _STATUS_CACHE = None


@core.mcp.tool()
async def memory_status() -> dict:
    """Return a bounded operational summary of wiki, graph, queues, ingest,
    communities, recent usage, and build version. Component failures degrade
    independently; no raw note bodies, query text, or credentials are returned."""
    started = time.monotonic()
    snapshot = await get_status()
    await anyio.to_thread.run_sync(lambda: core.record_query({
        "ts": time.time(),
        "at": core.now_iso(),
        "source": "mcp",
        "tool": "memory_status",
        "outcome": "ok" if snapshot.get("ok") else "degraded",
        "duration_ms": int((time.monotonic() - started) * 1000),
    }))
    return snapshot


async def http_status(_req: Request) -> JSONResponse:
    return JSONResponse(await get_status())

async def health(_req: Request) -> JSONResponse:
    """Combined readiness. 200 when the wiki index is usable (full or
    degraded); the graph side reports its client state alongside."""
    snapshot = wiki.idx.snapshot()
    snapshot["graph_ready"] = graph._g is not None
    return JSONResponse(snapshot, status_code=200 if snapshot["ready"] else 503)


async def metrics(req: Request) -> JSONResponse:
    """Tail of the shared query log (for the weekly gaps miner)."""
    days = float(req.query_params.get("days", "7"))
    try:
        events = await anyio.to_thread.run_sync(
            functools.partial(core.read_metrics, days)
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"events": events, "days": days})


# wiki.py's own /health is superseded by the combined one above.
_wiki_routes = [r for r in wiki.http_routes if getattr(r, "path", "") != "/health"]

mcp_app = core.mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(_app):
    wiki.start_background_reindex()
    await graph.warm()
    async with mcp_app.router.lifespan_context(mcp_app):
        try:
            yield
        finally:
            wiki.stop_background_reindex()
            await graph.close()


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/api/metrics", metrics),
        Route("/api/status", http_status),
        *_wiki_routes,
        *graph.http_routes,
        Mount("/", app=mcp_app),   # /mcp lands here
    ],
    lifespan=lifespan,
)


def main():
    import uvicorn
    core.log.info("starting dip.ink memory server on %s:%d (metrics=%s)",
                  core.HOST, core.PORT, core.METRICS_PATH)
    uvicorn.run(app, host=core.HOST, port=core.PORT, log_level="info")


if __name__ == "__main__":
    main()
