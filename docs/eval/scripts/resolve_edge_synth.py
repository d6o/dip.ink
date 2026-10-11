"""Add controlled duplicate cases with a known answer (read-only on the graph).

The live sample has almost no true duplicates: an extracted fact that Graphiti
merged leaves no edge behind. This script makes two labeled groups from real
edges T that sit between the same two entities as a sampled case:

  S  new fact = a meaning-preserving rewrite of T          -> T must be in duplicate_facts
  K  new fact = T with exactly one key detail changed      -> T must NOT be in duplicate_facts

A model outside the tested set (GEN_MODEL, default claude-opus-5-5) writes the
new fact. The script embeds it with the Graphiti embedder and rebuilds the
related edges and invalidation candidates "as of" the episode of the source
case, with the same code as resolve_edge_build.py.

  python3 resolve_edge_synth.py --cases /tmp/ret/cases.json --s 30 --k 20 --out /tmp/ret/synth.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import urllib.request

sys.path.insert(0, "/app")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from graphiti_core.embedder.openai import OpenAIEmbedder  # noqa: E402
from graphiti_core.driver.neo4j_driver import Neo4jDriver  # noqa: E402

import resolve_edge_build as rb  # noqa: E402
from vector_search import install_ingest_search  # noqa: E402

PROXY = "http://cliproxy.cliproxy.svc.cluster.local:8317/v1"
GEN_MODEL = os.environ.get("GEN_MODEL", "claude-opus-5-5")
UA = "OpenAI File Downloader, XaiImageApiFetch/1.0"

PROMPTS = {
    "S": (
        "Rewrite the fact below in different words. Keep exactly the same meaning: the same "
        "entities, numbers, dates, versions, names, and qualifiers. Do not add or remove "
        "information. Change the sentence structure so that the text is clearly different.\n\n"
        "FACT: {fact}\n\nReply with only the rewritten fact."
    ),
    "K": (
        "Rewrite the fact below and change exactly ONE key detail to a different plausible "
        "value: a number, a date, a version, a name, or a key qualifier. Keep everything else "
        "the same, and keep the wording close to the original.\n\n"
        "FACT: {fact}\n\nReply with only the changed fact."
    ),
}


def gen(kind, fact):
    body = {"model": GEN_MODEL, "max_tokens": 400, "temperature": 0.7,
            "messages": [{"role": "user", "content": PROMPTS[kind].format(fact=fact)}]}
    req = urllib.request.Request(PROXY + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer sk-no-auth", "User-Agent": UA,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.load(r)
    text = resp["choices"][0]["message"]["content"].strip().strip('"')
    return text, resp.get("model")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="/tmp/ret/cases.json")
    ap.add_argument("--s", type=int, default=30)
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--seed", type=int, default=99)
    ap.add_argument("--out", default="/tmp/ret/synth.json")
    a = ap.parse_args()

    base = json.load(open(a.cases))["cases"]
    pool = [c for c in base if c["related"]]
    rnd = random.Random(a.seed)
    rnd.shuffle(pool)
    driver = Neo4jDriver(os.environ["NEO4J_URI"], os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"],
                         database=os.environ.get("NEO4J_DATABASE", "neo4j"))
    install_ingest_search(driver)
    embedder = OpenAIEmbedder()
    out, used = [], set()
    plan = ["S"] * a.s + ["K"] * a.k
    i = 0
    for kind in plan:
        while i < len(pool):
            src = pool[i]
            i += 1
            t = rnd.choice(src["related"])
            if t["uuid"] in used:
                continue
            used.add(t["uuid"])
            break
        else:
            break
        try:
            fact, gm = gen(kind, t["fact"])
        except Exception as e:  # noqa: BLE001
            print(f"[gen] fail {type(e).__name__}: {e}", flush=True)
            continue
        emb = await embedder.create(input_data=[fact.replace("\n", " ")])
        row = {"uuid": "synthetic-" + t["uuid"], "fact": fact, "src": t["source"], "tgt": t["target"],
               "emb": emb, "valid_at": None, "ep_uuid": None,
               "ep_created": _parse(src["episode_created_at"]), "ep_name": src["episode"]}
        case = await rb.build_case(driver, row)
        tidx = [e["idx"] for e in case["related"] if e["uuid"] == t["uuid"]]
        n = sum(1 for c in out if c["group"] == kind)
        case.update({"group": kind, "id": f"{kind}{n:03d}", "hist": {
            "target_uuid": t["uuid"], "target_fact": t["fact"], "target_idx": tidx[0] if tidx else None,
            "gen_model": gm, "source_case": src["id"]}})
        if not case["llm_called"] or not tidx:
            print(f"[{kind}] skip: llm_called={case['llm_called']} target_in_related={bool(tidx)}", flush=True)
            continue
        out.append(case)
        print(f"[{kind}] {case['id']} T={tidx[0]} rel={len(case['related'])} cand={len(case['candidates'])}", flush=True)
    json.dump({"cases": out}, open(a.out, "w"))
    print(f"[done] {len(out)} synthetic cases -> {a.out}")
    await driver.close()


def _parse(s):
    from datetime import datetime
    return datetime.fromisoformat(s)


if __name__ == "__main__":
    asyncio.run(main())
