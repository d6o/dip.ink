"""entity_resolution — merge duplicate/alias entities before community clustering.

LLM extraction inevitably creates aliases ("MyApp" vs "myapp.example.com",
"k3s" vs "k3s cluster"). Duplicates fragment fact neighborhoods,
weaken graph_entity lookups, and blur communities. This pass runs inside the
Sunday community rebuild (before clustering, so communities form over the
deduped graph):

  1. candidate pairs: entities whose names are near-identical after
     normalization (case/punct/spacing), or one is a strict prefix/suffix of
     the other with the remainder being a generic token (cluster/service/domain
     etc). Cheap string logic — no embeddings needed for the obvious tier.
  2. LLM confirm: "are these the SAME thing?" (name + summary shown). Only
     merge on a confident yes.
  3. merge: repoint all relationships from the duplicate to the canonical
     entity (keep the longer-summary one), delete the duplicate node.

Conservative: string-candidates only (high precision), LLM gate, capped per
run (MAX_MERGES) so a bad run can't shred the graph. DRY_RUN honored.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys

sys.path.insert(0, "/app")

MAX_MERGES = int(os.environ.get("MAX_MERGES", "40"))
ER_DRY_RUN = os.environ.get("ER_DRY_RUN", os.environ.get("DRY_RUN", "0")) in ("1", "true")

GENERIC_TOKENS = {"cluster", "service", "server", "app", "site", "project", "the", "instance"}

CONFIRM_PROMPT = """Are these two knowledge-graph entities THE SAME real-world thing
(one is an alias, abbreviation, or domain-form of the other)? Different things that
are merely related (a service vs the platform that runs it, a tool vs its config)
are NOT the same.

A: "{a_name}" — {a_summary}
B: "{b_name}" — {b_summary}

Reply ONLY with JSON: {{"same": true/false}}"""


def _norm(name: str) -> str:
    n = name.lower().strip()
    n = re.sub(r"\.(ri\.gd|com|com\.br|ia\.br|dev\.br)$", "", n)  # strip common domain suffixes
    n = re.sub(r"[^a-z0-9]+", "", n)
    return n


def _generic_remainder(shorter: str, longer: str) -> bool:
    """True if longer = shorter + generic tokens (e.g. 'k3s' vs 'k3s cluster')."""
    ls, ll = shorter.lower(), longer.lower()
    if not ll.startswith(ls) and not ll.endswith(ls):
        return False
    rem = ll.replace(ls, "", 1)
    toks = set(re.findall(r"[a-z0-9]+", rem))
    return bool(toks) and toks <= GENERIC_TOKENS


async def find_candidates(driver) -> list[tuple[dict, dict]]:
    rows, _, _ = await driver.execute_query(
        "MATCH (n:Entity) RETURN n.uuid AS uuid, n.name AS name, "
        "coalesce(n.summary,'') AS summary"
    )
    ents = [dict(r) for r in rows]
    by_norm: dict[str, list[dict]] = {}
    for e in ents:
        by_norm.setdefault(_norm(e["name"]), []).append(e)

    pairs: list[tuple[dict, dict]] = []
    # tier 1: identical after normalization
    for group in by_norm.values():
        if len(group) > 1:
            canon = max(group, key=lambda e: len(e["summary"]))
            for other in group:
                if other["uuid"] != canon["uuid"]:
                    pairs.append((canon, other))
    # tier 2: generic-remainder prefix/suffix (k3s vs k3s cluster)
    names = sorted(ents, key=lambda e: len(e["name"]))
    seen_uuids = {p[1]["uuid"] for p in pairs}
    for i, a in enumerate(names):
        if len(pairs) >= MAX_MERGES * 3:
            break
        for b in names[i + 1:]:
            if b["uuid"] in seen_uuids or a["uuid"] in seen_uuids:
                continue
            if abs(len(a["name"]) - len(b["name"])) > 12:
                continue
            if _generic_remainder(a["name"], b["name"]):
                canon = max((a, b), key=lambda e: len(e["summary"]))
                dup = a if canon is b else b
                pairs.append((canon, dup))
                seen_uuids.add(dup["uuid"])
    return pairs[: MAX_MERGES * 3]


async def confirm_same(llm_client, a: dict, b: dict) -> bool:
    prompt = CONFIRM_PROMPT.format(
        a_name=a["name"], a_summary=a["summary"][:200],
        b_name=b["name"], b_summary=b["summary"][:200],
    )
    try:
        resp = await llm_client._generate_response(
            [{"role": "user", "content": prompt}], response_model=None,
            max_tokens=200, model_size=None,
        )
        text = resp if isinstance(resp, str) else json.dumps(resp)
        m = re.search(r'\{[^}]*\}', text)
        return bool(m and json.loads(m.group(0)).get("same") is True)
    except Exception:
        return False  # fail closed: no merge on any doubt


async def merge_entity(driver, canon: dict, dup: dict) -> None:
    """Repoint dup's relationships to canon, then delete dup."""
    await driver.execute_query(
        "MATCH (d:Entity {uuid: $dup})-[r:RELATES_TO]->(x) "
        "MATCH (c:Entity {uuid: $canon}) WHERE x.uuid <> $canon "
        "MERGE (c)-[r2:RELATES_TO {uuid: r.uuid}]->(x) SET r2 = properties(r) "
        "DELETE r", dup=dup["uuid"], canon=canon["uuid"])
    await driver.execute_query(
        "MATCH (x)-[r:RELATES_TO]->(d:Entity {uuid: $dup}) "
        "MATCH (c:Entity {uuid: $canon}) WHERE x.uuid <> $canon "
        "MERGE (x)-[r2:RELATES_TO {uuid: r.uuid}]->(c) SET r2 = properties(r) "
        "DELETE r", dup=dup["uuid"], canon=canon["uuid"])
    await driver.execute_query(
        "MATCH (e:Episodic)-[m:MENTIONS]->(d:Entity {uuid: $dup}) "
        "MATCH (c:Entity {uuid: $canon}) "
        "MERGE (e)-[:MENTIONS]->(c) DELETE m", dup=dup["uuid"], canon=canon["uuid"])
    await driver.execute_query(
        "MATCH (d:Entity {uuid: $dup}) DETACH DELETE d", dup=dup["uuid"])


async def run_entity_resolution(g) -> int:
    """Called from build_communities.py before clustering. Returns merge count."""
    pairs = await find_candidates(g.driver)
    print(f"[entity-res] {len(pairs)} candidate pairs (cap {MAX_MERGES} merges, "
          f"dry_run={ER_DRY_RUN})", flush=True)
    merged = 0
    for canon, dup in pairs:
        if merged >= MAX_MERGES:
            break
        if not await confirm_same(g.llm_client, canon, dup):
            continue
        print(f"[entity-res] MERGE '{dup['name']}' -> '{canon['name']}'", flush=True)
        if not ER_DRY_RUN:
            try:
                await merge_entity(g.driver, canon, dup)
                merged += 1
            except Exception as e:
                print(f"[entity-res] merge failed ({dup['name']}): {e}", flush=True)
        else:
            merged += 1
    print(f"[entity-res] done: {merged} merges{' (dry-run)' if ER_DRY_RUN else ''}", flush=True)
    return merged
