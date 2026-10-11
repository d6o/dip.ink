"""Write the test data without private text, for the public repository.

The cases hold fact text from the private graph. This script keeps only
structure, ids, decisions, timing, and usage, so that
resolve_edge_analyze.py can run again on the committed files:

  python3 resolve_edge_export.py --cases cases.json --cases synth.json \
      --calls calls.jsonl --calls calls_synth.jsonl --out ../data/2026-10-11-resolve-edge.json.gz

The analysis reads only idx values, set sizes, "group", and hist ids; the
exported cases replace every fact with an empty string.
"""
from __future__ import annotations

import argparse
import gzip
import json

CALL_KEYS = ("config", "id", "group", "latency_s", "error", "model", "finish_reason",
             "prompt_tokens", "completion_tokens", "reasoning_tokens", "dup", "contra", "parse_error")


def strip_edge(e):
    return {"idx": e["idx"], "uuid": e["uuid"], "fact": ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", action="append", required=True)
    ap.add_argument("--calls", action="append", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cases = []
    for f in a.cases:
        for c in json.load(open(f))["cases"]:
            hist = dict(c.get("hist") or {})
            hist.pop("target_fact", None)
            cases.append({
                "id": c["id"], "group": c["group"], "uuid": c["uuid"], "new_fact": "",
                "episode_created_at": c["episode_created_at"],
                "related": [strip_edge(e) for e in c["related"]],
                "candidates": [strip_edge(e) for e in c["candidates"]],
                "hist": hist,
            })
    calls = []
    for f in a.calls:
        for line in open(f):
            r = json.loads(line)
            calls.append({k: r.get(k) for k in CALL_KEYS})
    with gzip.open(a.out, "wt") as fh:
        json.dump({"cases": cases, "calls": calls}, fh)
    print(f"{len(cases)} cases, {len(calls)} calls -> {a.out}")


if __name__ == "__main__":
    main()
