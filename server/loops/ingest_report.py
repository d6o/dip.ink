"""ingest-report — summarize the per-note ingest measurements.

Reads the ``(:DipinkIngestMetric)`` nodes that ingest.py writes and prints
where ingest time goes. Run it in the memory image, for example:

    kubectl -n graphiti create job ingest-report-$(date +%s) --from=cronjob/graphiti-ingest \\
      -- python /app/loops/ingest_report.py --days 3

or inside any pod with Neo4j access. ``--json`` prints the raw aggregate.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


def fetch(days: float) -> list[dict]:
    from neo4j import GraphDatabase

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    driver = GraphDatabase.driver(
        os.environ.get("NEO4J_URI", "bolt://neo4j:7687"),
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "")),
    )
    try:
        records, _, _ = driver.execute_query(
            "MATCH (m:DipinkIngestMetric) WHERE m.at >= $since RETURN properties(m) AS m ORDER BY m.at",
            since=since,
        )
    finally:
        driver.close()
    rows = []
    for record in records:
        row = dict(record["m"])
        try:
            row.update(json.loads(row.pop("detail", "") or "{}"))
        except json.JSONDecodeError:
            pass
        rows.append(row)
    return rows


def aggregate(rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("outcome") == "ok"]
    out: dict = {
        "attempts": len(rows),
        "outcomes": Counter(r.get("outcome") for r in rows),
        "distinct_notes": len({r["slug"] for r in rows}),
        "notes_with_retries": sum(1 for c in Counter(r["slug"] for r in rows).values() if c > 1),
        "ok_per_day": Counter(r["at"][:10] for r in ok),
        "error_kinds": Counter(r.get("error_kind") for r in rows if r.get("outcome") != "ok"),
        "errors": Counter(r.get("error") for r in rows if r.get("outcome") != "ok").most_common(8),
    }
    if not rows:
        return out
    wall = [r["wall_s"] for r in ok]
    out["ok_wall_s"] = {"p50": pct(wall, .5), "p90": pct(wall, .9), "max": max(wall, default=None)}
    all_wall = sum(r["wall_s"] for r in rows) or 1
    out["time_share_all_attempts"] = {
        k: round(sum(r.get(k) or 0 for r in rows) / all_wall, 3)
        for k in ("llm_wall_s", "embed_wall_s", "neo4j_wall_s", "idle_s")
    }
    out["failed_attempt_time_share"] = round(
        sum(r["wall_s"] for r in rows if r.get("outcome") != "ok") / all_wall, 3
    )
    calls = [r["llm_calls"] for r in ok]
    out["llm_calls_per_ok_note"] = {"p50": pct(calls, .5), "p90": pct(calls, .9)}
    out["llm_overlap_ratio"] = round(
        sum(r.get("llm_sum_s") or 0 for r in rows) / (sum(r.get("llm_wall_s") or 0 for r in rows) or 1), 2
    )
    out["truncated_calls"] = sum(r.get("llm_truncated") or 0 for r in rows)
    out_tok = sum(r.get("llm_out_tok") or 0 for r in rows)
    out["reasoning_share_of_out_tok"] = round(
        sum(r.get("llm_reason_tok") or 0 for r in rows) / out_tok, 2) if out_tok else None
    out["episode_retries"] = sum(r.get("episode_retries") or 0 for r in rows)

    prompts: dict[str, dict] = defaultdict(lambda: Counter())
    models: dict[str, dict] = defaultdict(lambda: Counter())
    llm_failures: Counter = Counter()
    for r in rows:
        for name, p in (r.get("by_prompt") or {}).items():
            agg = prompts[name]
            for k in ("calls", "s", "fail", "in_tok", "out_tok", "reason_tok"):
                agg[k] += p.get(k) or 0
            agg["max_out_tok"] = max(agg["max_out_tok"], p.get("max_out_tok") or 0)
        for name, m in (r.get("by_model") or {}).items():
            for k in ("calls", "s", "fail"):
                models[name][k] += m.get(k) or 0
        llm_failures.update(r.get("llm_failures") or {})
    total_llm = sum(p["s"] for p in prompts.values()) or 1
    out["prompts"] = sorted(({
        "prompt": name,
        "calls": p["calls"],
        "share_of_llm_s": round(p["s"] / total_llm, 3),
        "mean_s": round(p["s"] / p["calls"], 1) if p["calls"] else None,
        "fail_rate": round(p["fail"] / p["calls"], 3) if p["calls"] else None,
        "mean_in_tok": round(p["in_tok"] / p["calls"]) if p["calls"] else None,
        "mean_out_tok": round(p["out_tok"] / p["calls"]) if p["calls"] else None,
        "reasoning_share_of_out": round(p["reason_tok"] / p["out_tok"], 2) if p["out_tok"] else None,
        "max_out_tok": p["max_out_tok"],
    } for name, p in prompts.items()), key=lambda x: -x["share_of_llm_s"])
    out["models"] = sorted(({
        "model": name,
        "calls": m["calls"],
        "mean_s": round(m["s"] / m["calls"], 1) if m["calls"] else None,
        "fail_rate": round(m["fail"] / m["calls"], 3) if m["calls"] else None,
    } for name, m in models.items()), key=lambda x: -x["calls"])
    out["llm_failure_kinds"] = llm_failures
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in ok:
        size = r.get("body_chars") or 0
        label = "<2k" if size < 2000 else "<5k" if size < 5000 else "<10k" if size < 10000 else ">=10k"
        buckets[label].append(r["wall_s"])
    out["ok_wall_by_size"] = {k: {"n": len(v), "p50": pct(v, .5)} for k, v in sorted(buckets.items())}
    out["slowest"] = [
        {k: r.get(k) for k in ("slug", "outcome", "wall_s", "llm_calls", "body_chars", "idle_s")}
        for r in sorted(rows, key=lambda r: -r["wall_s"])[:8]
    ]
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=float, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = aggregate(fetch(args.days))
    if args.json:
        print(json.dumps(result, default=dict, indent=2))
        return
    for key, value in result.items():
        if isinstance(value, list):
            print(f"{key}:")
            for item in value:
                print(f"  {item}")
        else:
            print(f"{key}: {dict(value) if isinstance(value, Counter) else value}")


if __name__ == "__main__":
    main()
