"""contradiction janitor — monthly hygiene for the bitemporal layer.

Structural detection of un-superseded contradictions drowns in false positives
(high-churn entities like Aisle legitimately hold 50+ distinct current facts).
The right tool is an LLM pass per entity: "here are the current facts about X —
which pairs are MUTUALLY EXCLUSIVE (cannot both be true now)?"

For each of the TOP_N most-current-fact-dense entities:
  1. pull its current facts (invalid_at IS NULL),
  2. ask the LLM to identify mutually-exclusive pairs,
  3. for each confirmed pair, invalidate the OLDER fact (set invalid_at =
     newer.valid_at, expired_at = now) — with receipts logged.

Conservative by design: only acts when the LLM answers with a parseable pair
list AND the two facts have different valid_at (so "older" is well-defined).
DRY_RUN=1 reports without writing.

Env: TOP_N (default 50), DRY_RUN, plus the usual NEO4J_*/LITELLM_* from the pod.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")
from ingest import build_graphiti  # noqa: E402

TOP_N = int(os.environ.get("TOP_N", "50"))
DRY_RUN = os.environ.get("DRY_RUN", "0") in ("1", "true")
MAX_FACTS_PER_ENTITY = 40

JUDGE_PROMPT = """You are auditing a knowledge graph for contradictions.
Below are the CURRENT facts about the entity "{name}" (each with an index and date).
Identify pairs that are MUTUALLY EXCLUSIVE — they cannot both be true at the same
time (e.g. "X runs on A" vs "X runs on B" when X runs on one thing; "the limit is
2G" vs "the limit is 5G"). Distinct events, historical observations, and facts
about different attributes are NOT contradictions.

Reply ONLY with JSON: {{"pairs": [[i, j], ...]}} — empty list if none.

FACTS:
{facts}
"""


async def judge_contradictions(g, name: str, facts: list[dict]) -> list[tuple[int, int]]:
    listing = "\n".join(f"{i}. [{f['valid_at'][:10]}] {f['fact'][:200]}" for i, f in enumerate(facts))
    prompt = JUDGE_PROMPT.format(name=name, facts=listing)
    try:
        resp = await g.llm_client._generate_response(  # reuse the wired LLM client
            [{"role": "user", "content": prompt}], response_model=None,
            max_tokens=1000, model_size=None,
        )
        text = resp if isinstance(resp, str) else json.dumps(resp)
    except Exception as e:
        print(f"[janitor] LLM error for {name}: {e}")
        return []
    m = re.search(r'\{.*\}', text, re.S)
    if not m:
        return []
    try:
        pairs = json.loads(m.group(0)).get("pairs", [])
        return [(int(a), int(b)) for a, b in pairs if isinstance(a, int) or str(a).isdigit()]
    except Exception:
        return []


async def main() -> None:
    g = build_graphiti()
    invalidated = 0
    reports = []
    try:
        rows, _, _ = await g.driver.execute_query(
            "MATCH (n:Entity)-[r:RELATES_TO]-() WHERE r.fact IS NOT NULL AND r.invalid_at IS NULL "
            "WITH n, count(r) AS c ORDER BY c DESC LIMIT $topn RETURN n.uuid AS uuid, n.name AS name, c",
            topn=TOP_N,
        )
        print(f"[janitor] auditing {len(rows)} densest entities (DRY_RUN={DRY_RUN})")
        for row in rows:
            frows, _, _ = await g.driver.execute_query(
                "MATCH (n:Entity {uuid: $uuid})-[r:RELATES_TO]-(m:Entity) "
                "WHERE r.fact IS NOT NULL AND r.invalid_at IS NULL "
                "RETURN r.uuid AS uuid, r.fact AS fact, toString(r.valid_at) AS valid_at "
                "ORDER BY r.valid_at ASC LIMIT $mx",
                uuid=row["uuid"], mx=MAX_FACTS_PER_ENTITY,
            )
            facts = [dict(f) for f in frows]
            if len(facts) < 2:
                continue
            pairs = await judge_contradictions(g, row["name"], facts)
            for a, b in pairs:
                if a >= len(facts) or b >= len(facts) or a == b:
                    continue
                older, newer = sorted((facts[a], facts[b]), key=lambda f: f["valid_at"])
                if older["valid_at"] == newer["valid_at"]:
                    continue  # can't order — skip, don't guess
                reports.append({
                    "entity": row["name"],
                    "invalidate": older["fact"][:120],
                    "kept": newer["fact"][:120],
                    "older_valid_at": older["valid_at"],
                })
                if not DRY_RUN:
                    await g.driver.execute_query(
                        "MATCH ()-[r:RELATES_TO {uuid: $uuid}]-() "
                        "SET r.invalid_at = datetime($inv), r.expired_at = datetime($now)",
                        uuid=older["uuid"], inv=newer["valid_at"],
                        now=datetime.now(timezone.utc).isoformat(),
                    )
                    invalidated += 1
    finally:
        await g.close()

    print(f"[janitor] contradictions found: {len(reports)}, invalidated: {invalidated}")
    for r in reports[:30]:
        print(f"  {r['entity']}: SUPERSEDE '{r['invalidate']}' -> kept '{r['kept']}'")
    print(f"[janitor] DONE {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    asyncio.run(main())
