"""memory-healthcheck — daily DEEP end-to-end test of the whole memory pipeline.

Complements memory_alerts.py (the 30-min shallow dead-man: newest-episode age,
community age, /health). This is the daily full-loop verification:

  A. WRITE PATH   — real notes reached the wiki repo in the last 48h (proven
                    by real traffic); if the period was quiet, drop a canary via
                    wiki_note_drop and verify ok+pushed.
  B. INGESTION    — every note dropped in the last 48h (older than the 2h ingest
                    grace) has an Episodic node in the graph. Per-note, not just
                    "newest episode age" — catches partial ingest failures.
  C. CURATION     — (1) no inbox note (wiki repo notes/) older than 24h still
                    unprocessed; (2) curation LIVENESS: notes keep arriving but
                    no commit has touched wiki/sources/notes/ for
                    CURATION_STALL_DAYS — measured via GIT activity, NOT slug
                    timestamps (a backlogged curator drains oldest-first, so
                    the newest curated SLUG stays old even while curation is
                    active — the 2026-07-12 misdiagnosis); (3) curation LAG:
                    oldest still-pending note older than CURATION_LAG_MAX_DAYS
                    (found 16d behind + 864 deferred on 2026-07-12 — drain
                    rate lost to max-turns failures); (4) .deferred backlog
                    size warning. Requires the clone to carry recent history
                    (initContainer uses --shallow-since, not --depth 1).
  D. EXPOSING     — the memory server healthy on the EXTERNAL (agent-facing)
                    URL, with internal fallback probing to distinguish "ingress
                    broken" from "service down". graph_search returns facts;
                    graph_answer answers a known question (confidence high/med,
                    non-null) AND refuses a nonsense question (not_found +
                    escalate — the no-hallucination safety property).
  E. INDEXING     — the newest curated source note (>6h old) is fetchable from
                    the wiki index (its 5-min reindex loop is alive).
  F. USAGE        — last-24h tool-call counts (excluding test-tagged events).
                    Zero usage = warning, not failure.

Alerting: exit 1 = failed Job (visible in kubectl/k9s) + a best-effort
`memory-healthcheck-failed` note dropped into the wiki inbox so the operator
and agents see the failure in memory itself.

Runs on a daily schedule with the wiki repo cloned to /notes (initContainer or
compose service). All probe traffic uses test=1 so it never pollutes usage
metrics or the weekly gaps report.

Env:
  NOTES_DIR              default /notes (wiki repo checkout root)
  MCP_BASE               agent-facing memory-server base URL
  MCP_INTERNAL           internal-network URL (fallback probe — distinguishes
                         "ingress broken" from "service down")
  ANSWER_PROBE           a question your graph should answer with high confidence
  FRESH_WRITE_HOURS      default 48
  INGEST_GRACE_HOURS     default 2
  CURATION_MAX_AGE_HOURS default 24
  CURATION_STALL_DAYS    default 3
  CURATION_LAG_MAX_DAYS  default 7
  DEFERRED_WARN          default 200
  INDEX_GRACE_HOURS      default 6
  DRY_RUN                "1" = no canary drop, no failure-note drop
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ingest.py (build_graphiti) lives at /app in the image; this file runs from
# /app/loops/, so /app isn't on sys.path by default (same shim as
# server.py and memory_alerts.py).
sys.path.insert(0, "/app")

NOTES_DIR = Path(os.environ.get("NOTES_DIR", "/notes"))
# The combined memory server. MCP_BASE is the agent-facing URL; MCP_INTERNAL
# is the internal-network fallback used to distinguish "ingress broken" from
# "service down" when they differ.
MCP_EXT = os.environ.get("MCP_BASE", os.environ.get("WIKI_MCP_BASE", "http://memory:8080")).rstrip("/")
MCP_INT = os.environ.get("MCP_INTERNAL", MCP_EXT).rstrip("/")
ANSWER_PROBE = os.environ.get("ANSWER_PROBE", "what is this memory system's note inbox called")
NEGATIVE_PROBE = "what is the name of the operator's pet unicorn's favorite constellation"
FRESH_WRITE_H = float(os.environ.get("FRESH_WRITE_HOURS", "48"))
INGEST_GRACE_H = float(os.environ.get("INGEST_GRACE_HOURS", "2"))
CURATION_MAX_AGE_H = float(os.environ.get("CURATION_MAX_AGE_HOURS", "24"))
CURATION_STALL_D = float(os.environ.get("CURATION_STALL_DAYS", "3"))
CURATION_LAG_MAX_D = float(os.environ.get("CURATION_LAG_MAX_DAYS", "7"))
DEFERRED_WARN = int(os.environ.get("DEFERRED_WARN", "200"))
INDEX_GRACE_H = float(os.environ.get("INDEX_GRACE_HOURS", "6"))
DRY_RUN = os.environ.get("DRY_RUN", "0") in ("1", "true")

SLUG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}-\d{6})-")
NOW = datetime.now(timezone.utc)

failures: list[str] = []
warnings: list[str] = []


def _slug_ts(name: str) -> datetime | None:
    m = SLUG_TS_RE.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d-%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age_h(ts: datetime) -> float:
    return (NOW - ts).total_seconds() / 3600


def _get(url: str, timeout: float = 30) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "memory-healthcheck"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _get_json(url: str, timeout: float = 30):
    status, body = _get(url, timeout)
    return json.loads(body)


def _probe(name: str, ext_base: str, int_base: str, path: str) -> str | None:
    """Try the external (agent-facing) URL; on failure probe the internal
    Service to say WHICH layer is broken. Returns the working base or None."""
    try:
        status, _ = _get(f"{ext_base}{path}", timeout=20)
        if status == 200:
            return ext_base
        failures.append(f"{name}: external {path} HTTP {status}")
    except Exception as e:
        try:
            status, _ = _get(f"{int_base}{path}", timeout=15)
            if status == 200:
                failures.append(f"{name}: INGRESS path broken ({ext_base}{path}: {e}) but internal service OK — check traefik/external-dns/cert")
                return int_base  # keep testing deeper layers via internal
            failures.append(f"{name}: DOWN — external ({e}) and internal HTTP {status}")
        except Exception as e2:
            failures.append(f"{name}: DOWN — external ({e}) and internal ({e2})")
    return None


# ---------------------------------------------------------------- A + B + C: filesystem
def collect_notes() -> tuple[list[tuple[str, datetime]], list[tuple[str, datetime]], list[tuple[str, datetime]]]:
    """Return (inbox, deferred, curated) as (slug, ts) lists, newest first.
    Inbox includes bare .md files (protocol-deviation notes count too — a bare
    file from June sat invisible to dir-only scans)."""
    inbox: list[tuple[str, datetime]] = []
    inbox_root = NOTES_DIR / "notes"
    if inbox_root.is_dir():
        for p in inbox_root.iterdir():
            if p.name.startswith(".") or p.name == "README.md":
                continue
            name = p.name[:-3] if (p.is_file() and p.suffix == ".md") else p.name
            ts = _slug_ts(name)
            if ts:
                inbox.append((name, ts))
    deferred: list[tuple[str, datetime]] = []
    deferred_root = NOTES_DIR / "notes" / ".deferred"
    if deferred_root.is_dir():
        for p in deferred_root.iterdir():
            name = p.name[:-3] if (p.is_file() and p.suffix == ".md") else p.name
            ts = _slug_ts(name)
            if ts:
                deferred.append((name, ts))
    curated: list[tuple[str, datetime]] = []
    curated_root = NOTES_DIR / "wiki" / "sources" / "notes"
    if curated_root.is_dir():
        for p in curated_root.glob("*/*/*/*"):
            if not p.is_dir():
                continue
            ts = _slug_ts(p.name)
            if ts:
                curated.append((p.name, ts))
    inbox.sort(key=lambda x: x[1], reverse=True)
    deferred.sort(key=lambda x: x[1], reverse=True)
    curated.sort(key=lambda x: x[1], reverse=True)
    return inbox, deferred, curated


def drop_note(slug: str, note_md: str) -> dict | None:
    """wiki_note_drop via the memory server MCP JSON-RPC (stateless). None on failure."""
    body = {
        "jsonrpc": "2.0", "id": "drop", "method": "tools/call",
        "params": {"name": "wiki_note_drop", "arguments": {"slug": slug, "note_md": note_md}},
    }
    for base in (MCP_EXT, MCP_INT):
        try:
            req = urllib.request.Request(
                f"{base}/mcp", data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"})
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read().decode(errors="replace")
            # tolerate SSE or plain JSON framing
            for chunk in raw.split("\n"):
                chunk = chunk.removeprefix("data:").strip()
                if not chunk.startswith("{"):
                    continue
                msg = json.loads(chunk)
                content = (msg.get("result") or {}).get("content") or []
                for c in content:
                    if c.get("type") == "text":
                        try:
                            return json.loads(c["text"])
                        except Exception:
                            return {"raw": c["text"][:200]}
            return {"raw": raw[:200]}
        except Exception as e:
            warnings.append(f"note drop via {base} failed: {e}")
    return None


def check_write_path(inbox, deferred, curated) -> None:
    all_notes = inbox + deferred + curated
    newest = max((ts for _, ts in all_notes), default=None)
    if newest and _age_h(newest) <= FRESH_WRITE_H:
        print(f"[A] write path OK: newest note is {_age_h(newest):.1f}h old (real traffic)")
        return
    # Quiet period — prove the write path with a canary.
    print(f"[A] no notes in {FRESH_WRITE_H:g}h — dropping canary")
    if DRY_RUN:
        warnings.append("write path unverified (quiet period, DRY_RUN=1 so no canary)")
        return
    now_iso = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    res = drop_note("memory-healthcheck-canary", f"""---
captured: {now_iso}
session: automated daily memory healthcheck (write-path canary — dropped only when no real notes for {FRESH_WRITE_H:g}h)
topic: healthcheck canary
---

## Durable claims

- (none — synthetic write-path canary; curator should delete without creating pages)

## Context

The memory-healthcheck CronJob found no real note drops in the last {FRESH_WRITE_H:g} hours,
so it exercised the write path itself. Tomorrow's run verifies this canary was ingested.
""")
    if res and res.get("ok") and res.get("pushed"):
        print(f"[A] canary dropped + pushed: {res.get('folder')}")
    else:
        failures.append(f"write path BROKEN: canary drop failed or not pushed: {res}")


def _known_query() -> str:
    return os.environ.get("SEARCH_PROBE", "note inbox curation")


async def check_ingestion(inbox, deferred, curated) -> None:
    """Every note from the last 48h (past the ingest grace) must be an Episodic.
    Includes .deferred — graphiti's INBOX_ROOTS rglob ingests those directly."""
    recent = [(slug, ts) for slug, ts in (inbox + deferred + curated)
              if _age_h(ts) <= FRESH_WRITE_H and _age_h(ts) >= INGEST_GRACE_H][:25]
    if not recent:
        print("[B] ingestion: no notes past grace in window — nothing to verify")
        return
    from ingest import DEFAULT_GROUP_ID, build_graphiti
    g = build_graphiti()
    try:
        slugs = [s for s, _ in recent]
        rows, _, _ = await g.driver.execute_query(
            "MATCH (e:Episodic {group_id: $group_id}) WHERE e.name IN $slugs "
            "RETURN e.name AS name",
            slugs=slugs,
            group_id=DEFAULT_GROUP_ID,
        )
        have = {r["name"] for r in rows}
        missing = [(s, ts) for s, ts in recent if s not in have]
        if missing:
            worst = max(_age_h(ts) for _, ts in missing)
            failures.append(
                f"ingestion: {len(missing)}/{len(recent)} recent notes NOT in graph "
                f"(oldest missing {worst:.1f}h): " + ", ".join(s for s, _ in missing[:5]))
        else:
            print(f"[B] ingestion OK: {len(recent)}/{len(recent)} recent notes present as episodes")
    finally:
        await g.close()


def _curated_git_activity_days() -> float | None:
    """Days since the last commit that touched wiki/sources/notes/, from the
    clone's git history. None if git/history unavailable (shallow too thin)."""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", str(NOTES_DIR), "log", "-1", "--format=%ct", "--", "wiki/sources/notes"],
            capture_output=True, text=True, timeout=60)
        ts = out.stdout.strip()
        if not ts:
            return None
        return (NOW - datetime.fromtimestamp(int(ts), tz=timezone.utc)).total_seconds() / 86400
    except Exception as e:  # noqa: BLE001
        warnings.append(f"curated git-activity probe failed: {e}")
        return None


def check_curation(inbox, deferred, curated) -> None:
    ok = True
    # C1: inbox entries sitting unprocessed past the curator's cadence
    stale = [(s, ts) for s, ts in inbox if _age_h(ts) > CURATION_MAX_AGE_H]
    if stale:
        ok = False
        failures.append(
            f"curation backlog: {len(stale)} inbox notes older than {CURATION_MAX_AGE_H:g}h "
            f"(oldest {_age_h(stale[-1][1]) / 24:.0f}d: {stale[-1][0]})")
    # C2: curation LIVENESS — measured by git activity on the curated tree,
    # NOT slug timestamps (backlogged curators drain oldest-first, so the
    # newest curated slug stays old while curation is perfectly alive).
    newest_arrival = max((ts for _, ts in inbox + deferred), default=None)
    if newest_arrival and _age_h(newest_arrival) <= CURATION_STALL_D * 24:
        act_d = _curated_git_activity_days()
        if act_d is not None and act_d > CURATION_STALL_D:
            ok = False
            failures.append(
                f"curation STALLED: notes still arriving (newest {_age_h(newest_arrival):.1f}h ago) "
                f"but no commit has touched wiki/sources/notes for {act_d:.1f}d "
                f"(threshold {CURATION_STALL_D:g}d)")
    # C3: curation LAG — how far behind is the curator? Oldest pending note.
    oldest_pending = min((ts for _, ts in inbox + deferred), default=None)
    if oldest_pending:
        lag_d = _age_h(oldest_pending) / 24
        if lag_d > CURATION_LAG_MAX_D:
            ok = False
            failures.append(
                f"curation LAG: oldest unprocessed note is {lag_d:.0f}d old "
                f"(threshold {CURATION_LAG_MAX_D:g}d; pending={len(inbox) + len(deferred)}) — "
                f"drain rate is losing to arrival rate; wiki knowledge is stale by that much")
    # C4: deferred backlog size
    if len(deferred) > DEFERRED_WARN:
        warnings.append(f".deferred backlog: {len(deferred)} notes (warn threshold {DEFERRED_WARN})")
    if ok:
        print(f"[C] curation OK: inbox={len(inbox)} fresh, deferred={len(deferred)}, "
              f"lag={_age_h(oldest_pending) / 24:.1f}d" if oldest_pending else f"[C] curation OK: inbox={len(inbox)}, no pending")


# ---------------------------------------------------------------- D: exposing
def check_exposing() -> None:
    base = _probe("memory-server", MCP_EXT, MCP_INT, "/health")
    gbase = wbase = base

    if gbase:
        q = urllib.parse.quote(_known_query())
        try:
            pkt = _get_json(f"{gbase}/api/graph/search?q={q}&test=1", timeout=60)
            if not (pkt.get("facts") or pkt.get("entities")):
                failures.append("graph_search: 0 facts AND 0 entities for a known-good query")
            else:
                print(f"[D] graph_search OK: {len(pkt.get('facts', []))} facts")
        except Exception as e:
            failures.append(f"graph_search errored: {e}")

        try:
            ans = _get_json(f"{gbase}/api/answer?q={urllib.parse.quote(ANSWER_PROBE)}&test=1", timeout=90)
            if ans.get("confidence") in ("high", "medium") and ans.get("answer"):
                print(f"[D] graph_answer OK ({ans['confidence']}): {str(ans['answer'])[:60]}")
            else:
                failures.append(f"graph_answer probe weak/broken: confidence={ans.get('confidence')} answer={str(ans.get('answer'))[:60]}")
        except Exception as e:
            failures.append(f"graph_answer errored: {e}")

        try:
            neg = _get_json(f"{gbase}/api/answer?q={urllib.parse.quote(NEGATIVE_PROBE)}&test=1", timeout=90)
            if neg.get("confidence") == "not_found" and not neg.get("answer") and neg.get("escalate"):
                print("[D] graph_answer no-hallucination guard OK")
            elif neg.get("confidence") == "error":
                failures.append("graph_answer negative probe returned error (distiller down?)")
            else:
                failures.append(f"HALLUCINATION GUARD FAILED: nonsense question got confidence={neg.get('confidence')} answer={str(neg.get('answer'))[:80]}")
        except Exception as e:
            failures.append(f"graph_answer negative probe errored: {e}")

    if wbase:
        try:
            res = _get_json(f"{wbase}/api/search?q={urllib.parse.quote(_known_query())}&k=3", timeout=60)
            if not res.get("results"):
                failures.append(f"wiki_search: 0 results for {_known_query()!r} (index empty?)")
            else:
                print(f"[D] wiki_search OK: {len(res['results'])} results")
        except Exception as e:
            failures.append(f"wiki_search errored: {e}")
    return


# ---------------------------------------------------------------- E: indexing
def check_indexing(curated) -> None:
    candidates = [(s, ts) for s, ts in curated if _age_h(ts) >= INDEX_GRACE_H]
    if not candidates:
        print("[E] indexing: no curated note past grace — skip")
        return
    slug, ts = candidates[0]
    try:
        status, _ = _get(f"{MCP_EXT}/api/page/{urllib.parse.quote(slug)}", timeout=30)
        if status == 200:
            print(f"[E] index OK: newest curated note ({_age_h(ts):.0f}h old) is fetchable: {slug}")
            return
        failures.append(f"index stale: {slug} ({_age_h(ts):.0f}h old) HTTP {status} from the wiki index")
    except Exception as e:
        failures.append(f"index check errored for {slug}: {e}")


# ---------------------------------------------------------------- F: usage
def check_usage() -> None:
    by_tool: dict[str, int] = {}
    try:
        data = _get_json(f"{MCP_EXT}/api/metrics?days=1", timeout=30)
        for e in data.get("events", []):
            if e.get("test"):
                continue
            by_tool[e.get("tool", "?")] = by_tool.get(e.get("tool", "?"), 0) + 1
    except Exception as e:
        warnings.append(f"usage metrics unavailable: {e}")
        return
    print(f"[F] usage last 24h (test-tagged excluded): {json.dumps(by_tool)}")
    if not by_tool:
        warnings.append("zero MCP usage in 24h — agents idle, or registration/logging broken")


# ---------------------------------------------------------------- main
def main() -> None:
    print(f"memory-healthcheck @ {NOW.isoformat()} (notes={NOTES_DIR}, dry_run={DRY_RUN})")
    inbox, deferred, curated = collect_notes()
    print(f"notes on disk: inbox={len(inbox)} deferred={len(deferred)} curated={len(curated)}")

    check_write_path(inbox, deferred, curated)
    import asyncio
    try:
        asyncio.run(check_ingestion(inbox, deferred, curated))
    except Exception as e:
        failures.append(f"ingestion check errored: {e}")
    check_curation(inbox, deferred, curated)
    check_exposing()
    check_indexing(curated)
    check_usage()

    for w in warnings:
        print(f"  ~~ WARN: {w}")
    if failures:
        print("MEMORY HEALTHCHECK FAILING:")
        for f in failures:
            print(f"  !! {f}")
        if not DRY_RUN:
            now_iso = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
            fail_lines = "\n".join(f"- {f}" for f in failures)
            warn_lines = "\n".join(f"- {w}" for w in warnings) or "- (none)"
            res = drop_note("memory-healthcheck-failed", f"""---
captured: {now_iso}
session: automated daily memory healthcheck — FAILURES detected
topic: memory healthcheck failure
---

# Memory healthcheck FAILED — {NOW.strftime('%Y-%m-%d')}

## Durable claims

- The daily memory-healthcheck run of {NOW.strftime('%Y-%m-%d')} found {len(failures)} failing check(s) in the ingestion/indexing/exposing pipeline (details below). Until a later healthcheck note or fix supersedes this, treat these subsystems as suspect.

## Failing checks

{fail_lines}

## Warnings

{warn_lines}

## What to do

Investigate per check letter (A write path, B ingestion, C curation backlog, D MCP exposing, E wiki index, F usage) — the runbook is the module docstring of `graphiti/loops/memory_healthcheck.py`.
""")
            print(f"failure note drop result: {res}")
        raise SystemExit(1)
    print("ALL MEMORY PIPELINE CHECKS PASSED")


if __name__ == "__main__":
    main()
