"""Build communities on the full graph. Self-contained unbounded→bounded fallback.

Phase 1 runs unbounded (concurrency 4, no cap) under BUILD_TIMEOUT. If it doesn't
finish, fall back to bounded: cap each cluster's tree-reduce input to
BOUNDED_MAX_CLUSTER members (stratified sample). build_communities() clears
existing communities at the start (remove_communities), so the bounded retry
starts clean. NOTE: communities are persisted only after the whole build
completes (built in memory first), so restarting before completion loses only
cheap in-memory work.

Env:
  BUILD_TIMEOUT         seconds for the phase-1 attempt (default 14400 = 4h)
  COMMUNITY_CONC        phase-1 concurrency (default 4)
  MAX_CLUSTER           if set, phase-1 ALSO caps clusters (empty = unbounded)
  BOUNDED_CONC          fallback concurrency (default 4)
  BOUNDED_MAX_CLUSTER   fallback cluster cap (default 200)
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app")
from ingest import build_graphiti  # noqa: E402  (also applies patch_community_clustering at import)


def _retry_wrap(fn, name):
    """Wrap an async fn with retry-on-transient-error. Survives APIConnectionError /
    APITimeoutError / RateLimitError blips instead of crashing the whole build."""
    import asyncio as _aio

    async def wrapped(*a, **kw):
        last = None
        for attempt in range(6):
            try:
                return await fn(*a, **kw)
            except Exception as e:
                last = e
                ename = type(e).__name__
                msg = str(e)
                transient = (
                    "Connection" in msg or "connection" in msg
                    or "timeout" in msg.lower() or "timed out" in msg.lower()
                    or "429" in msg or ename in (
                        "APIConnectionError", "APITimeoutError", "APIStatusError",
                        "RateLimitError", "RouterRateLimitError",
                    )
                )
                if not transient:
                    raise
                wait = min(2 ** attempt, 30)
                print(f"[communities] transient {ename} in {name}, retry {attempt+1}/6 in {wait}s: {msg[:90]}", flush=True)
                await _aio.sleep(wait)
        raise last
    return wrapped


def _make_build_community(conc: int, max_cluster: int | None):
    """Build a build_community override: concurrency=conc, optional member cap.

    max_cluster=None -> fully unbounded (no sampling), just higher concurrency.
    max_cluster=<int> -> cap each cluster's tree-reduce input to that many members.
    """
    import random
    from graphiti_core.utils.maintenance.community_operations import (
        build_community_edges, generate_summary_description, summarize_pair,
        truncate_at_sentence, MAX_SUMMARY_CHARS,
    )
    from graphiti_core.utils.datetime_utils import utc_now
    from graphiti_core.helpers import semaphore_gather
    from graphiti_core.nodes import CommunityNode

    async def _build_one_cluster(llm_client, community_cluster):
        summaries = [e.summary for e in community_cluster]
        if max_cluster is not None and len(summaries) > max_cluster:
            random.seed(42)
            summaries = random.sample(summaries, max_cluster)
        length = len(summaries)
        while length > 1:
            odd = None
            if length % 2 == 1:
                odd = summaries.pop()
                length -= 1
            half = int(length / 2)
            pairs = list(zip(summaries[:half], summaries[half:], strict=False))
            new_summaries = await semaphore_gather(
                *[summarize_pair(llm_client, (str(a), str(b))) for a, b in pairs],
                max_coroutines=conc,
            )
            if odd is not None:
                new_summaries.append(odd)
            summaries = new_summaries
            length = len(summaries)
        summary = truncate_at_sentence(summaries[0], MAX_SUMMARY_CHARS)
        name = await generate_summary_description(llm_client, summary)
        now = utc_now()
        cnode = CommunityNode(
            name=name,
            group_id=community_cluster[0].group_id,
            labels=["Community"],
            created_at=now,
            summary=summary,
        )
        return cnode, build_community_edges(community_cluster, cnode, now)

    async def build_community(llm_client, community_cluster):
        try:
            return await _build_one_cluster(llm_client, community_cluster)
        except Exception as e:  # per-cluster: degrade instead of killing the whole build
            # Return a coarse fallback community (no LLM) so the consumer's tuple shape
            # is preserved and every member keeps a community. Better than skipping.
            print(f"[communities] cluster summarize failed -> coarse fallback ({len(community_cluster)} members): "
                  f"{type(e).__name__}: {str(e)[:80]}", flush=True)
            now = utc_now()
            joined = "; ".join(str(getattr(e2, "summary", "")) for e2 in community_cluster[:5])
            summary = truncate_at_sentence(joined, MAX_SUMMARY_CHARS)
            cnode = CommunityNode(
                name=summary[:60] or "community",
                group_id=community_cluster[0].group_id,
                labels=["Community"],
                created_at=now,
                summary=summary,
            )
            return cnode, build_community_edges(community_cluster, cnode, now)

    return build_community


def _apply_patch(conc: int, max_cluster: int | None) -> None:
    from graphiti_core.utils.maintenance import community_operations as co
    # Resilience: wrap the LLM-calling functions so a transient connection error
    # is retried instead of crashing the whole multi-hour build. Covers
    # summarize_pair + generate_summary_description + the per-community naming/embedding path.
    co.summarize_pair = _retry_wrap(co.summarize_pair, "summarize_pair")
    co.generate_summary_description = _retry_wrap(co.generate_summary_description, "generate_summary_description")
    co.build_community = _make_build_community(conc, max_cluster)
    mode = f"bounded(cap={max_cluster})" if max_cluster else "unbounded"
    print(f"[communities] patch applied: {mode}, conc={conc} (+retry on transient + per-cluster skip)", flush=True)


def _patch_ingest_suspend(suspend: bool) -> None:
    """Pause/resume the ingest CronJob so the build owns the LLM budget (no
    contention with ingest's concurrency 4, which would rate-limit pi). Uses the
    in-pod service-account token; needs RBAC to patch cronjobs/graphiti-ingest.
    Best-effort: logs + continues if RBAC is missing (runs concurrently).
    Set PAUSE_INGEST=0 to disable (run alongside ingest)."""
    import json as _json
    import ssl
    import urllib.request
    if os.environ.get("PAUSE_INGEST", "1").lower() not in ("1", "true", "yes", "on"):
        print("[communities] PAUSE_INGEST disabled — running concurrently with ingest", flush=True)
        return
    ns = os.environ.get("PAUSE_INGEST_NS", "dipink")
    name = os.environ.get("PAUSE_INGEST_NAME", "graphiti-ingest")
    try:
        token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text().strip()
        ctx = ssl.create_default_context(cafile="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
        url = f"https://kubernetes.default.svc/apis/batch/v1/namespaces/{ns}/cronjobs/{name}"
        req = urllib.request.Request(
            url, data=_json.dumps({"spec": {"suspend": suspend}}).encode(), method="PATCH",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/merge-patch+json"},
        )
        urllib.request.urlopen(req, context=ctx, timeout=30)
        print(f"[communities] ingest cron suspend={suspend}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[communities] WARN: could not patch ingest suspend={suspend}: {e!r} "
              f"(running concurrently)", flush=True)


async def main() -> None:
    build_timeout = int(os.environ.get("BUILD_TIMEOUT", "14400"))
    start_conc = int(os.environ.get("COMMUNITY_CONC", "4"))
    max_cluster_env = os.environ.get("MAX_CLUSTER", "")
    start_max = int(max_cluster_env) if max_cluster_env else None
    bounded_conc = int(os.environ.get("BOUNDED_CONC", "4"))
    bounded_max = int(os.environ.get("BOUNDED_MAX_CLUSTER", "200"))

    _patch_ingest_suspend(True)  # pause ingest -> build owns the LLM budget
    try:
        g = build_graphiti()
        try:
            # Entity resolution BEFORE clustering: merge alias entities
            # ("MyApp" vs "myapp.example.com", "k3s" vs "k3s cluster") so communities form
            # over the deduped graph. Best-effort — a failure never blocks
            # the rebuild. ER_DRY_RUN=1 to report-only.
            if os.environ.get("ENTITY_RESOLUTION", "1").lower() in ("1", "true", "yes"):
                try:
                    sys.path.insert(0, "/app/loops")
                    from entity_resolution import run_entity_resolution
                    await run_entity_resolution(g)
                except Exception as e:  # noqa: BLE001
                    print(f"[communities] WARN entity resolution skipped: {e!r}", flush=True)
            _apply_patch(start_conc, start_max)
            label = "UNBOUNDED" if start_max is None else f"INITIAL(cap={start_max})"
            print(f"[communities] starting {label} build (conc={start_conc}, timeout={build_timeout}s)...", flush=True)
            try:
                nodes, edges = await asyncio.wait_for(g.build_communities(), timeout=build_timeout)
                print(f"[communities] {label} OK: {len(nodes)} communities, {len(edges)} edges", flush=True)
            except asyncio.TimeoutError:
                print(f"[communities] {label} timed out -> falling back to BOUNDED build", flush=True)
                _apply_patch(bounded_conc, bounded_max)
                nodes, edges = await g.build_communities()
                print(f"[communities] BOUNDED OK: {len(nodes)} communities, {len(edges)} edges "
                      f"(clusters capped at {bounded_max} members, conc={bounded_conc})", flush=True)
        finally:
            await g.close()
    finally:
        _patch_ingest_suspend(False)  # ALWAYS resume ingest, even on failure
    print(f"[communities] DONE at {datetime.now(timezone.utc).isoformat()}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
