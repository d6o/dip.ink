"""memory-alerts — shallow operational checks. Exit 1 = alert.

Checks the shared `/api/status` snapshot rather than treating graph inactivity as
failure. Pending note→episode lag is the freshness signal: a quiet memory with
zero pending notes is healthy regardless of the newest episode's age. Community
age and component readiness remain hard checks. Blocked notes and review-queue
items are visible warnings but do not fail the job by themselves.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/app")

MCP_BASE = os.environ.get("MCP_BASE", os.environ.get("WIKI_MCP_BASE", "http://memory:8080")).rstrip("/")
MAX_PENDING_AGE_H = float(os.environ.get("MAX_PENDING_AGE_HOURS", "2"))
MAX_COMMUNITY_AGE_D = float(os.environ.get("MAX_COMMUNITY_AGE_DAYS", "8"))

failures: list[str] = []
warnings: list[str] = []


def evaluate_status(snapshot: dict) -> None:
    """Apply deterministic alert policy to one bounded status snapshot."""
    components = snapshot.get("components") or {}
    for name in ("wiki", "graph", "git_clone"):
        component = components.get(name) or {}
        if not component.get("ready"):
            failures.append(f"component {name} not ready ({component.get('error') or 'unknown'})")

    ingest = snapshot.get("ingest") or {}
    if ingest.get("error"):
        failures.append(f"ingest status unavailable ({ingest.get('error')})")
    pending = int(ingest.get("pending") or 0)
    lag_seconds = float(ingest.get("lag_seconds") or 0.0)
    if pending > 0 and lag_seconds > MAX_PENDING_AGE_H * 3600:
        failures.append(
            f"ingest pending lag: {pending} note(s), oldest {lag_seconds / 3600:.1f}h "
            f"(threshold {MAX_PENDING_AGE_H:g}h)"
        )

    communities = snapshot.get("communities") or {}
    community_count = int(communities.get("count") or 0)
    community_age = communities.get("age_seconds")
    if community_count == 0:
        failures.append("communities: none in graph")
    elif community_age is not None and float(community_age) > MAX_COMMUNITY_AGE_D * 86400:
        failures.append(
            f"communities stale: newest is {float(community_age) / 86400:.1f}d old "
            f"(threshold {MAX_COMMUNITY_AGE_D:g}d)"
        )

    queues = snapshot.get("queues") or {}
    blocked = int((queues.get("blocked") or {}).get("count") or 0)
    review = int(queues.get("review_queue_open") or 0)
    if blocked:
        warnings.append(f"blocked notes awaiting audit: {blocked}")
    if review:
        warnings.append(f"curator review queue open: {review}")


def check_status(base: str) -> None:
    try:
        with urllib.request.urlopen(f"{base}/api/status", timeout=20) as response:
            if response.status != 200:
                failures.append(f"memory-server /api/status HTTP {response.status}")
                return
            snapshot = json.loads(response.read())
    except Exception as error:  # noqa: BLE001
        failures.append(f"memory-server unreachable: {error}")
        return
    if not isinstance(snapshot, dict):
        failures.append("memory-server /api/status returned a non-object")
        return
    evaluate_status(snapshot)


def main() -> None:
    failures.clear()
    warnings.clear()
    check_status(MCP_BASE)

    for warning in warnings:
        print(f"  ~~ WARN: {warning}")
    if failures:
        print("MEMORY ALERTS FIRING:")
        for failure in failures:
            print(f"  !! {failure}")
        raise SystemExit(1)
    print(f"all memory checks OK at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
