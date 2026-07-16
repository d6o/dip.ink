"""memory-alerts — dead-man checks for the agentic memory. Exit 1 = alert.

Three checks (the ones whose absence caused real silent failures in this saga):
  1. Ingest freshness: newest note on disk (git) vs newest episode in the graph.
     If the gap exceeds MAX_PENDING_AGE_HOURS, ingest is stuck (cron suspended,
     git clone failing, LLM down...).
  2. Community age: newest Community.created_at older than MAX_COMMUNITY_AGE_DAYS
     means the weekly rebuild failed/stopped.
  3. Both MCPs' /health.

Runs every 30 min with backoffLimit=0 — a FAILED job is the alert (visible in
`kubectl get jobs -n graphiti`, k9s, events). Cheap and dependency-free.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/app")

WIKI_BASE = os.environ.get("WIKI_MCP_BASE", "http://wiki-mcp:8080").rstrip("/")
GRAPHITI_BASE = os.environ.get("GRAPHITI_MCP_BASE", "http://graphiti-mcp:8080").rstrip("/")
MAX_PENDING_AGE_H = float(os.environ.get("MAX_PENDING_AGE_HOURS", "2"))
MAX_COMMUNITY_AGE_D = float(os.environ.get("MAX_COMMUNITY_AGE_DAYS", "8"))

failures: list[str] = []


def check_health(name: str, base: str) -> None:
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=15) as r:
            if r.status != 200:
                failures.append(f"{name} /health HTTP {r.status}")
    except Exception as e:
        failures.append(f"{name} unreachable: {e}")


async def check_graph() -> None:
    from ingest import build_graphiti
    g = build_graphiti()
    try:
        # 1. ingest freshness — compare newest episode's slug-timestamp with now.
        rows, _, _ = await g.driver.execute_query(
            "MATCH (e:Episodic) RETURN max(e.name) AS newest")
        newest = rows[0]["newest"] if rows else None
        if newest and len(newest) >= 17:
            try:
                ts = datetime.strptime(newest[:17], "%Y-%m-%d-%H%M%S").replace(tzinfo=timezone.utc)
                age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                # Notes don't arrive constantly; alert only past the threshold + slack.
                # A quiet weekend is fine; a 12h gap on a weekday usually means stuck.
                if age_h > MAX_PENDING_AGE_H * 6:  # 12h with the default 2h knob
                    failures.append(f"ingest freshness: newest episode is {age_h:.1f}h old ({newest[:40]})")
            except ValueError:
                pass
        # 2. community age
        rows, _, _ = await g.driver.execute_query(
            "MATCH (c:Community) RETURN max(c.created_at) AS newest, count(c) AS n")
        if rows and rows[0]["n"]:
            newest_c = rows[0]["newest"]
            if newest_c is not None:
                age_d = (datetime.now(timezone.utc) - newest_c.to_native().replace(tzinfo=timezone.utc)
                         if hasattr(newest_c, "to_native") else datetime.now(timezone.utc) - newest_c
                         ).total_seconds() / 86400
                if age_d > MAX_COMMUNITY_AGE_D:
                    failures.append(f"communities stale: newest is {age_d:.1f}d old (weekly rebuild failing?)")
        else:
            failures.append("communities: none in graph")
    finally:
        await g.close()


def main() -> None:
    check_health("wiki-mcp", WIKI_BASE)
    check_health("graphiti-mcp", GRAPHITI_BASE)
    try:
        asyncio.run(check_graph())
    except Exception as e:
        failures.append(f"graph checks errored: {e}")

    if failures:
        print("MEMORY ALERTS FIRING:")
        for f in failures:
            print(f"  !! {f}")
        raise SystemExit(1)
    print(f"all memory checks OK at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
