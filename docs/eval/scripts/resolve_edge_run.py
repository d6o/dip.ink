"""Call dedupe_edges.resolve_edge for each case with one or more model configs.

Run inside the memory pod. The prompt is rendered exactly like ingest does it:
graphiti-core's prompt library, then dip.ink CompactSchemaClient.generate_response
(compact JSON example shape, language instruction, _clean_input). Only the HTTP
call is replaced, so the script can choose the endpoint, the model, and extra
body fields. Request fields match ingest: temperature 1, max_tokens 16384,
response_format json_object.

  python3 resolve_edge_run.py --cases /tmp/ret/cases.json --out /tmp/ret/calls.jsonl \
      --config base1 --config gpt61-low --concurrency 3

A config name maps to CONFIGS below. Rows already in --out are skipped, so the
script can resume. Output rows hold ids, parsed decisions, timing, and usage.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/app")

from graphiti_core.llm_client import LLMConfig  # noqa: E402
from graphiti_core.llm_client.config import ModelSize  # noqa: E402
from graphiti_core.prompts import prompt_library  # noqa: E402
from graphiti_core.prompts.dedupe_edges import EdgeDuplicate  # noqa: E402

import ingest  # noqa: E402  (dip.ink server/ingest.py, for CompactSchemaClient)

ROUTER = "http://batch-router.cliproxy.svc.cluster.local:8320/v1"
PROXY = "http://cliproxy.cliproxy.svc.cluster.local:8317/v1"
HEADERS = {"Authorization": "Bearer sk-no-auth", "Content-Type": "application/json"}
UA = "OpenAI File Downloader, XaiImageApiFetch/1.0"

CONFIGS = {
    "base1": (ROUTER, "batch-router", {}),
    "base2": (ROUTER, "batch-router", {}),
    "sol-direct": (PROXY, "gpt-6.1-sol", {}),
    "sol-low": (PROXY, "gpt-6.1-sol", {"reasoning_effort": "low"}),
    "sol-none": (PROXY, "gpt-6.1-sol", {"reasoning_effort": "none"}),
    "luna": (PROXY, "gpt-6-luna", {}),
    "luna-low": (PROXY, "gpt-6-luna", {"reasoning_effort": "low"}),
    "astra-low": (PROXY, "gpt-6-astra", {"reasoning_effort": "low"}),
    "haiku45": (PROXY, "claude-haiku-4-5-20251001", {}),
    "gemini-flash": (PROXY, "gemini-3-flash", {}),
    "gemini-flash-low": (PROXY, "gemini-3-flash", {"reasoning_effort": "low"}),
    "haiku": (PROXY, "claude-haiku-5-5", {}),
    "glm-flash": (PROXY, "glm-5.3-flash", {}),
    "glm": (PROXY, "glm", {}),
    "grok-fast": (PROXY, "grok-4.7-build-fast", {}),
}


class _Capture(ingest.CompactSchemaClient):
    """CompactSchemaClient that returns the final request instead of calling the API."""

    async def _generate_response(self, messages, response_model=None, max_tokens=16384,
                                 model_size=ModelSize.medium):
        out = []
        for m in messages:
            m.content = self._clean_input(m.content)
            if m.role in ("user", "system"):
                out.append({"role": m.role, "content": m.content})
        return {
            "messages": out,
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "response_format": self._build_response_format(response_model),
            "model_size": model_size.value,
        }


def render(case, client):
    context = {
        "existing_edges": [{"idx": e["idx"], "fact": e["fact"]} for e in case["related"]],
        "new_edge": case["new_fact"],
        "edge_invalidation_candidates": [{"idx": e["idx"], "fact": e["fact"]} for e in case["candidates"]],
    }
    return asyncio.run(client.generate_response(
        prompt_library.dedupe_edges.resolve_edge(context),
        response_model=EdgeDuplicate,
        model_size=ModelSize.small,
        prompt_name="dedupe_edges.resolve_edge",
        group_id=ingest.DEFAULT_GROUP_ID,
    ))


def parse(text):
    """Return (dup, contra, error) the way ingest + Graphiti would see them."""
    try:
        obj = json.loads(ingest.CompactSchemaClient._strip_code_fences(text))
    except Exception as e:  # noqa: BLE001
        return None, None, f"json:{type(e).__name__}"
    for _ in range(3):
        if isinstance(obj, dict) and len(obj) == 1 and "answer" in obj and isinstance(obj["answer"], dict):
            obj = obj["answer"]
    if isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict):
        obj = obj[0]
    try:
        r = EdgeDuplicate(**obj)
    except Exception as e:  # noqa: BLE001
        return None, None, f"schema:{type(e).__name__}"
    return sorted(set(r.duplicate_facts)), sorted(set(r.contradicted_facts)), None


def call(base, model, extra, req, timeout=300):
    body = {
        "model": model,
        "messages": req["messages"],
        "temperature": req["temperature"],
        "max_tokens": req["max_tokens"],
        "response_format": req["response_format"],
    }
    body.update(extra)
    data = json.dumps(body).encode()
    h = dict(HEADERS)
    h["User-Agent"] = UA
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(base + "/chat/completions", data=data, headers=h),
                                    timeout=timeout) as r:
            resp = json.load(r)
        err = None
    except urllib.error.HTTPError as e:
        resp, err = None, f"http {e.code}: {e.read()[:300].decode('utf-8', 'replace')}"
    except Exception as e:  # noqa: BLE001
        resp, err = None, f"{type(e).__name__}: {e}"
    dt = time.monotonic() - t0
    return resp, err, dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="/tmp/ret/cases.json")
    ap.add_argument("--out", default="/tmp/ret/calls.jsonl")
    ap.add_argument("--config", action="append", required=True)
    ap.add_argument("--ids", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--dump-prompt", default="")
    a = ap.parse_args()

    cases = json.load(open(a.cases))["cases"]
    if a.ids:
        keep = set(a.ids.split(","))
        cases = [c for c in cases if c["id"] in keep]
    if a.limit:
        cases = cases[: a.limit]
    client = _Capture(config=LLMConfig(api_key="sk-no-auth", model="batch-router", base_url=PROXY),
                      structured_output_mode="json_object")
    reqs = {c["id"]: render(c, client) for c in cases}
    if a.dump_prompt:
        json.dump(reqs[cases[0]["id"]], open(a.dump_prompt, "w"), indent=1)

    done = set()
    if os.path.exists(a.out):
        for line in open(a.out):
            r = json.loads(line)
            if not r.get("error"):
                done.add((r["config"], r["id"]))
    jobs = [(cfg, c) for c in cases for cfg in a.config if (cfg, c["id"]) not in done]
    print(f"[run] {len(jobs)} calls, concurrency {a.concurrency}", flush=True)
    lock = threading.Lock()
    out = open(a.out, "a")
    n = [0]

    def work(job):
        cfg, c = job
        base, model, extra = CONFIGS[cfg]
        req = reqs[c["id"]]
        resp, err, dt = call(base, model, extra, req)
        row = {"config": cfg, "id": c["id"], "group": c["group"], "latency_s": round(dt, 3),
               "error": err, "ts": time.time()}
        if resp is not None:
            ch = (resp.get("choices") or [{}])[0]
            text = (ch.get("message") or {}).get("content") or ""
            usage = resp.get("usage") or {}
            det = usage.get("completion_tokens_details") or {}
            dup, con, perr = parse(text) if text else (None, None, "empty")
            row.update({
                "model": resp.get("model"), "finish_reason": ch.get("finish_reason"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": det.get("reasoning_tokens"),
                "text": text[:2000], "dup": dup, "contra": con, "parse_error": perr,
            })
        with lock:
            out.write(json.dumps(row) + "\n")
            out.flush()
            n[0] += 1
            if n[0] % 20 == 0 or err:
                print(f"[run] {n[0]}/{len(jobs)} {cfg} {c['id']} {dt:.1f}s {err or ''}", flush=True)

    with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        list(ex.map(work, jobs))
    out.close()
    print("[run] done", flush=True)


if __name__ == "__main__":
    main()
