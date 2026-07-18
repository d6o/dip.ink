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
import functools

import anyio.to_thread
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

import core
import graph
import wiki


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
