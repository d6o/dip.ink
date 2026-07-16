"""memory-gaps miner — closes the retrieval feedback loop.

Weekly job: pull the memory server's query log (/api/metrics — one JSONL for
both wiki_* and graph_* tools), find the queries agents ran that retrieved
NOTHING USEFUL (zero hits, or low relevance), cluster them, and drop a
`memory-gaps` note into the wiki inbox via the note_drop tool.

That note is itself ingested by graphiti (the loop!), and the operator +
future sessions see exactly what agents wanted to know but couldn't find — the
system learns what it's missing.

Also reports usage split (wiki vs graph call counts) — client-preference
instrumentation.

Env:
  MCP_BASE   default http://memory:8080 (the combined server)
  DAYS       lookback window (default 7)
  DRY_RUN    "1" = print, don't drop the note
"""
from __future__ import annotations

import json
import os
import statistics
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone

MCP_BASE = os.environ.get("MCP_BASE", os.environ.get("WIKI_MCP_BASE", "http://memory:8080")).rstrip("/")
WIKI_BASE = MCP_BASE
GRAPHITI_BASE = MCP_BASE
DAYS = float(os.environ.get("DAYS", "7"))
DRY_RUN = os.environ.get("DRY_RUN", "0") in ("1", "true")
LOW_SCORE = 0.45  # wiki cosine below this = poor retrieval


def _get(url: str) -> dict | list | None:
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[gaps] WARN fetch {url}: {e}")
        return None


def _recurring_gaps(current_gaps: list[dict]) -> list[str]:
    """Gap queries that ALSO appeared in a previous memory-gaps report — found by
    searching the wiki for prior reports (they're ingested notes). Best-effort."""
    prior_text = ""
    cutoff = (datetime.now(timezone.utc).timestamp() - 5 * 86400)
    data = _get(f"{WIKI_BASE}/api/search?q=memory%20gaps%20report&k=6")
    if isinstance(data, dict):
        for r in data.get("results", []):
            name = r.get("name", "")
            # note names start with their capture timestamp — exclude reports from
            # the last 5 days so "recurrence" means a DIFFERENT week, not the
            # report this run (or a rerun) just dropped.
            m = name[:17]
            try:
                ts = datetime.strptime(m, "%Y-%m-%d-%H%M%S").replace(tzinfo=timezone.utc).timestamp()
                if ts > cutoff:
                    continue
            except ValueError:
                pass  # non-timestamped name (curated page) — keep
            page = _get(f"{WIKI_BASE}/api/page/{urllib.parse.quote(name)}")
            if isinstance(page, dict):
                prior_text += page.get("body", "") or ""
    if not prior_text:
        return []
    out = []
    for g in current_gaps:
        key = " ".join(g["q"].lower().split())[:40]
        if key and key in prior_text.lower():
            out.append(g["q"])
    return out


def all_events() -> list[dict]:
    """The combined server keeps ONE query log for both tool families."""
    data = _get(f"{MCP_BASE}/api/metrics?days={DAYS}")
    if isinstance(data, dict) and "events" in data:
        return data["events"]
    return []


def main() -> None:
    events = all_events()
    wiki_tools = {"search", "get", "backlinks"}
    wev = [e for e in events if e.get("tool") in wiki_tools]
    gev = [e for e in events if str(e.get("tool", "")).startswith("graph_")]
    print(f"[gaps] events: wiki={len(wev)} graph={len(gev)} (last {DAYS:g}d)")

    # --- usage split (client preference) ---
    wiki_searches = [e for e in wev if e.get("tool") == "search"]
    graph_searches = [e for e in gev if e.get("tool") == "graph_search" and not e.get("test")]
    # exclude test-tagged events (smoke tests hit /api/answer?test=1) so
    # deliberate negative tests don't inflate the not_found rate / gap list
    graph_answers = [e for e in gev if e.get("tool") == "graph_answer" and not e.get("test")]
    split = {
        "wiki_search_calls": len(wiki_searches),
        "graph_search_calls": len(graph_searches),
        "graph_answer_calls": len(graph_answers),
        "wiki_gets": sum(1 for e in wev if e.get("tool") == "get"),
        "graph_note_gets": sum(1 for e in gev if e.get("tool") == "graph_get_note"),
        "graph_entity_calls": sum(1 for e in gev if e.get("tool") == "graph_entity"),
        "graph_current_facts_calls": sum(1 for e in gev if e.get("tool") == "graph_current_facts"),
    }

    # --- graph_answer health: confidence distribution + compression ratio ---
    # (THE metric: median answer tokens vs the packet it distilled from.)
    ga_conf = Counter(str(e.get("confidence", "?")) for e in graph_answers)
    ga_answered = [e for e in graph_answers
                   if e.get("confidence") in ("high", "medium", "low") and e.get("answer_tokens_est")]
    ga_med_answer = statistics.median([e["answer_tokens_est"] for e in ga_answered]) if ga_answered else None
    ga_med_packet = statistics.median([e.get("packet_tokens_est", 0) for e in ga_answered]) if ga_answered else None
    ga_compression = (round(ga_med_packet / ga_med_answer, 1)
                      if ga_med_answer and ga_med_packet else None)
    ga_not_found = [e for e in graph_answers if e.get("confidence") in ("not_found", "error")]
    ga_cached = sum(1 for e in graph_answers if e.get("cached"))
    ga_stats = {
        "calls": len(graph_answers),
        "cache_hits": ga_cached,
        "confidence_distribution": dict(ga_conf),
        "median_answer_tokens_est": ga_med_answer,
        "median_packet_tokens_est": ga_med_packet,
        "compression_ratio": ga_compression,
        "not_found_rate": round(len(ga_not_found) / len(graph_answers), 3) if graph_answers else None,
    }

    # --- gaps: queries that retrieved nothing useful ---
    gaps: list[dict] = []
    for e in wiki_searches:
        top = e.get("top") or []
        best = max((t.get("score", 0) for t in top), default=0)
        if e.get("n", 0) == 0 or best < LOW_SCORE:
            gaps.append({"src": "wiki", "q": e.get("query", ""), "best_score": round(best, 3)})
    for e in graph_searches:
        if e.get("n_facts", 0) == 0 and e.get("n_entities", 0) == 0:
            gaps.append({"src": "graphiti", "q": e.get("query", ""), "best_score": None})
    # graph_answer not_found/error = the memory couldn't answer a direct
    # question — same gap signal as a zero-hit search.
    for e in ga_not_found:
        gaps.append({"src": "graph_answer", "q": e.get("question", ""), "best_score": None})

    # dedupe near-identical gap queries
    seen: set[str] = set()
    uniq_gaps = []
    for g in gaps:
        key = " ".join(g["q"].lower().split())[:60]
        if key and key not in seen:
            seen.add(key)
            uniq_gaps.append(g)

    # most-hit pages/topics (the load-bearing knowledge)
    top_pages = Counter()
    for e in wev:
        if e.get("tool") == "get" and e.get("hit"):
            top_pages[e.get("name", "?")] += 1

    print(f"[gaps] usage split: {split}")
    print(f"[gaps] graph_answer stats: {ga_stats}")
    print(f"[gaps] gap queries: {len(uniq_gaps)} unique")

    # --- Tier-3 trigger evaluation: the memory schedules its own upgrades. ---
    # Each condition, when met, emits an explicit build instruction into the
    # report (which agents + the operator read, and graphiti ingests). This is how
    # deferred work happens autonomously instead of being forgotten.
    tier3 = []
    # T3a: learned ranking — needs real graphiti usage to rank against.
    if len(graph_searches) >= 200:
        tier3.append(
            "**TRIGGER: learned ranking is now buildable.** graph_search has "
            f"{len(graph_searches)} calls this week (≥200). Build usage-weighted "
            "fact ranking: boost facts/notes by retrieval frequency, decay "
            "ephemeral operational facts. See server/graph.py.")
    # T3b: gap auto-classifier — needs recurrence across weeks to be meaningful.
    recur = _recurring_gaps(uniq_gaps)
    if len(recur) >= 5:
        tier3.append(
            f"**TRIGGER: gap auto-classifier is now warranted.** {len(recur)} gap "
            "queries recur from a previous weekly report. Build the classifier: "
            "LLM labels each recurring gap retrieval-miss vs genuinely-missing; "
            "retrieval-misses feed search tuning, missing knowledge becomes "
            "capture candidates. Recurring: "
            + "; ".join(f"`{q[:60]}`" for q in recur[:5]))
    # T3c: episode semantic search — only if fusion still leaves zero-hit queries.
    zero_both = [g for g in uniq_gaps if g["src"] == "graphiti"]
    if len(zero_both) >= 15:
        tier3.append(
            f"**TRIGGER: consider episode embeddings.** {len(zero_both)} graphiti "
            "queries returned nothing even with wiki fusion available — evaluate "
            "whether semantic episode search would have answered them before building.")
    tier3_section = ""
    if tier3:
        tier3_section = "\n## \u26a1 Tier-3 build triggers fired \u2014 ACTION REQUIRED\n\n" + \
            "\n".join(f"- {t}" for t in tier3) + \
            "\n\nAny agent reading this: surface these to the operator or start the build.\n"
        print(f"[gaps] TIER-3 TRIGGERS FIRED: {len(tier3)}")

    # --- compose the note ---
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    gap_lines = "\n".join(
        f"- ({g['src']}{', best ' + str(g['best_score']) if g['best_score'] is not None else ''}) `{g['q'][:140]}`"
        for g in uniq_gaps[:40]
    ) or "- (none — every query this week retrieved something)"
    top_lines = "\n".join(f"- `{n}` × {c}" for n, c in top_pages.most_common(10)) or "- (no gets)"

    note_md = f"""---
captured: {now}
session: automated memory-gaps miner (weekly)
topic: memory gaps report
---

# Memory gaps report — week of {now[:10]}

## Durable claims

- Agents made {split['wiki_search_calls']} wiki_search, {split['graph_search_calls']} graph_search, and {split['graph_answer_calls']} graph_answer calls in the last {DAYS:g} days.
- {len(uniq_gaps)} unique queries retrieved nothing useful (zero hits, relevance < {LOW_SCORE}, or graph_answer not_found) — these are the current gaps in the agentic memory.
- graph_answer compression ratio (median packet tokens / median answer tokens): {ga_stats['compression_ratio'] if ga_stats['compression_ratio'] is not None else 'n/a (no answered calls yet)'}.
- graph_answer not_found rate: {ga_stats['not_found_rate'] if ga_stats['not_found_rate'] is not None else 'n/a'} (target < 0.20; higher means retrieval is failing or agents ask out-of-scope).
- The most-fetched knowledge this week is listed under "Load-bearing pages".

## Gap queries (what agents wanted but couldn't find)

{gap_lines}

## Load-bearing pages (most-fetched — protect these)

{top_lines}

## Usage split (client preference instrumentation)

```json
{json.dumps(split, indent=2)}
```

## graph_answer health (the distilled-answer interface)

```json
{json.dumps(ga_stats, indent=2)}
```

## What to do with this

Each gap query is a candidate for a note someone should write, a convention
that was never captured, or a retrieval weakness. If the same gap recurs across
weeks, that knowledge is genuinely missing — capture it or accept it's out of
scope. This report is itself ingested by graphiti, so asking "what are the
current memory gaps" will surface it.
{tier3_section}"""

    if DRY_RUN:
        print("---- DRY RUN note ----")
        print(note_md)
        return

    # drop via the memory server MCP tool (plain JSON-RPC, stateless)
    body = {
        "jsonrpc": "2.0", "id": "drop", "method": "tools/call",
        "params": {"name": "wiki_note_drop", "arguments": {
            "slug": "memory-gaps-weekly", "note_md": note_md,
        }},
    }
    req = urllib.request.Request(
        f"{WIKI_BASE.replace('/api', '')}/mcp" if WIKI_BASE.endswith("/api") else WIKI_BASE + "/mcp",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            print(f"[gaps] note dropped: HTTP {r.status}")
            print(r.read()[:300].decode(errors="replace"))
    except Exception as e:
        print(f"[gaps] note drop FAILED: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
