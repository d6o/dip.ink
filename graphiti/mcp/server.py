"""graphiti-mcp — native MCP server over the Graphiti knowledge graph.

Sister to wiki-mcp, but reads from Graphiti (Neo4j) instead of the markdown
index, and exposes Graphiti's native strengths directly rather than forcing
them into a "page" shape:

  - graph_answer(question): server-side DISTILLED ANSWER — assembles the fat
    retrieval packet internally, then one LLM call boils it down to
    {answer, confidence, sources, superseded_note?, escalate}. ~150 tokens out
    instead of ~1,800. The fix for "the memory bombards agents".
  - graph_search(query): the rich packet — current atomic facts (with provenance
    slug + validity window), a community summary, top entities, and the top
    source-note excerpt. Uses Graphiti's `search_()` + COMBINED_HYBRID_SEARCH_RRF
    (the config that won a two-judge retrieval eval).
  - graph_get_note(slug): fetch a source note by its timestamp slug (the
    provenance path — every fact traces to one).
  - graph_entity(name): a known entity + its CURRENT facts + attributes
    (bitemporal: superseded facts excluded). Graphiti's unique capability.
  - graph_current_facts(subject): what's true NOW about a subject — the temporal
    angle plain document search has no answer to.

Read-only. Writes (note capture) stay with wiki-mcp → git (source of truth);
Graphiti's ingest cron turns dropped notes into the graph. This server just
serves the graph.

Every call is instrumented to a JSONL log (mirrors wiki-mcp's queries.jsonl) so
clients' usage / preference can be compared across servers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

# Reuse the ingest client wiring (Graphiti extraction LLM, OpenAI embedder,
# the roomy Neo4j pool, patch_community_clustering). ingest.py is on sys.path
# via /app in the image; this file lives at /app/mcp/server.py.
sys.path.insert(0, "/app")
from ingest import build_graphiti  # noqa: E402
from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF  # noqa: E402

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))

# Fusion: merge wiki-mcp's SEMANTIC note/page hits into the graph_search packet.
# Covers graphiti's structural blind spot (episodes are BM25-keyword-only) using
# the embeddings wiki-mcp already maintains — no episode-embedding infra needed.
# Best-effort: if wiki-mcp is down, the packet just lacks semantic_notes.
WIKI_MCP_BASE = os.environ.get("WIKI_MCP_BASE", "http://wiki-mcp:8080").rstrip("/")
FUSION = os.environ.get("GRAPHITI_MCP_FUSION", "1").lower() in ("1", "true", "yes")

# Distiller (graph_answer): plain chat completion against any OpenAI-compatible
# endpoint — NOT the graphiti llm_client (its retry/schema wrappers hide
# latency). Defaults to the same endpoint/model as extraction (LLM_* envs);
# leave DISTILL_BASE_URL unset with no LLM_BASE_URL to use OpenAI directly.
DISTILL_BASE_URL = (os.environ.get("DISTILL_BASE_URL") or os.environ.get("LLM_BASE_URL")
                    or "https://api.openai.com/v1")
DISTILL_API_KEY = (os.environ.get("DISTILL_API_KEY") or os.environ.get("LLM_API_KEY")
                   or os.environ.get("OPENAI_API_KEY", ""))
DISTILL_MODEL = os.environ.get("DISTILL_MODEL") or os.environ.get("LLM_MODEL") or "gpt-4.1-mini"
# Optional fallback model when the primary rate-limits (429-storm = quota
# window blew): degrading beats erroring for a window. Empty = no fallback.
DISTILL_FALLBACK_MODEL = os.environ.get("DISTILL_FALLBACK_MODEL", "")

# Answer cache: factual questions repeat (same question 3× in 90 min on day
# one) and the graph only changes on ingest ticks, so a short TTL is safe.
# Only real answers are cached — not_found/error always re-run.
ANSWER_CACHE_TTL = float(os.environ.get("ANSWER_CACHE_TTL", "3600"))  # seconds; 0 disables
_ANSWER_CACHE: dict[str, tuple[float, dict, int]] = {}  # key -> (expires_at, result, packet_tokens_est)
_ANSWER_CACHE_MAX = 500

# --- Instrumentation (mirrors wiki-mcp: PVC JSONL so it survives restarts) ---
CACHE_DIR = Path(os.environ["GRAPHITI_MCP_CACHE_DIR"]) if os.environ.get("GRAPHITI_MCP_CACHE_DIR") else None
_m_env = os.environ.get("GRAPHITI_MCP_METRICS_PATH", "").strip()
if _m_env.lower() in {"", "auto"}:
    METRICS_PATH = (CACHE_DIR / "queries.jsonl") if CACHE_DIR else None
elif _m_env.lower() in {"off", "none", "0", "disabled"}:
    METRICS_PATH = None
else:
    METRICS_PATH = Path(_m_env)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("graphiti-mcp")


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_query(event: dict) -> None:
    """Append a usage event to METRICS_PATH (best-effort)."""
    if METRICS_PATH is None:
        return
    try:
        METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with METRICS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("failed to record metric to %s: %s", METRICS_PATH, e)


# --- Graphiti client (one per process; created in lifespan) ---
_g = None


async def _get_graph():
    global _g
    if _g is None:
        log.info("building Graphiti client (extraction LLM from env, OpenAI embedder, roomy pool)")
        _g = build_graphiti()
    return _g


def _valid_at_window(edge) -> dict:
    """Bitemporal validity of an edge — the current/superseded signal."""
    return {
        "valid_at": str(getattr(edge, "valid_at", "") or ""),
        "invalid_at": str(getattr(edge, "invalid_at", "") or ""),
        "current": not bool(getattr(edge, "invalid_at", None)),
    }


def _episode_slug(edge, slug_map: dict | None = None) -> str:
    """Resolve an edge's source episode slug (provenance). Search-result edges
    carry episode UUIDs as plain strings (graphiti does NOT hydrate episode
    objects), so resolve uuid→name via slug_map from _resolve_episode_slugs.
    Object-shaped episodes handled for forward-compat."""
    eps = getattr(edge, "episodes", None) or []
    if not eps:
        return ""
    ep = eps[0]
    if isinstance(ep, str):
        return (slug_map or {}).get(ep, "")
    return str(getattr(ep, "name", "") or "")


async def _resolve_episode_slugs(g, edges) -> dict[str, str]:
    """Batch-resolve edge episode uuids → episode names (note slugs) in ONE
    Cypher query. Fixes the empty-source_slug problem: facts previously cited
    "" because search edges carry uuid strings, not hydrated episodes."""
    uuids = {ep for e in edges
             for ep in (getattr(e, "episodes", None) or [])[:1]
             if isinstance(ep, str)}
    if not uuids:
        return {}
    try:
        rows, _, _ = await g.driver.execute_query(
            "MATCH (e:Episodic) WHERE e.uuid IN $uuids RETURN e.uuid AS uuid, e.name AS name",
            uuids=list(uuids),
        )
        return {r["uuid"]: r["name"] or "" for r in rows}
    except Exception as e:  # noqa: BLE001
        log.warning("episode slug resolution failed: %s", e)
        return {}


async def _wiki_semantic_hits(query: str, k: int = 3) -> list[dict]:
    """Fusion helper: wiki-mcp's semantic (embedding) search over all pages+notes.
    Runs in a thread (urllib is sync) with a short timeout; [] on any failure."""
    if not FUSION:
        return []
    import urllib.parse
    import urllib.request

    def _fetch() -> list[dict]:
        url = f"{WIKI_MCP_BASE}/api/search?q={urllib.parse.quote(query)}&k={k}"
        with urllib.request.urlopen(url, timeout=6) as r:
            data = json.loads(r.read())
        return [{
            "name": p.get("name", ""),
            "score": round(float(p.get("score", 0)), 3),
            "type": p.get("type", ""),
            "description": (p.get("description") or "")[:200],
        } for p in data.get("results", [])[:k]]

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        log.warning("fusion: wiki-mcp semantic fetch failed: %s", e)
        return []


async def _assemble_packet(
    query: str,
    k: int,
    *,
    excerpt_chars: int = 2500,
    n_communities: int = 3,
    community_chars: int = 1000,
) -> dict:
    """Shared packet assembler for graph_search (wire format) and graph_answer
    (distiller input).

    Packet-trim experiment (2026-07-11): excerpt 800/1200 + communities 2@600
    caused SYSTEMATIC frozen-50 verdict flips to v1 (13 and 11 flips vs a
    3-flip same-day fat-packet control) — the excerpt is load-bearing for
    retrieval quality. Trim rejected; graph_answer (which always distills the
    full packet server-side) is the token-compression mechanism instead."""
    g = await _get_graph()
    config = COMBINED_HYBRID_SEARCH_RRF.model_copy(update={"limit": k})
    # graph search + wiki semantic search run concurrently (fusion)
    res, semantic_notes = await asyncio.gather(
        g.search_(query, config=config),
        _wiki_semantic_hits(query, 3),
    )
    communities = list(res.communities or [])[:n_communities]
    nodes = list(res.nodes or [])[:8]
    edges = list(res.edges or [])[:12]
    episodes = list(res.episodes or [])[:2]
    slug_map = await _resolve_episode_slugs(g, edges)

    facts = [{
        "fact": getattr(e, "fact", "") or str(e),
        "source_slug": _episode_slug(e, slug_map),
        **_valid_at_window(e),
    } for e in edges]

    return {
        "query": query,
        "facts": facts,
        "communities": [{
            "name": getattr(c, "name", "")[:120],
            "summary": (getattr(c, "summary", "") or "")[:community_chars],
        } for c in communities],
        "entities": [{
            "name": getattr(n, "name", ""),
            "summary": (getattr(n, "summary", "") or "")[:300],
        } for n in nodes],
        "source_excerpt": {
            "slug": getattr(episodes[0], "name", "") if episodes else "",
            "content": (getattr(episodes[0], "content", "") or "")[:excerpt_chars] if episodes else "",
        } if episodes else None,
        # Fusion: wiki-mcp's semantic hits (pages AND notes, incl. curated pages
        # graphiti doesn't have). Fetch a full page/note via wiki_get or
        # graph_get_note using the name.
        "semantic_notes": semantic_notes,
    }


# --- Distiller (graph_answer) ---

_distill_client = None


def _get_distill_client():
    global _distill_client
    if _distill_client is None:
        from openai import AsyncOpenAI
        _distill_client = AsyncOpenAI(
            api_key=DISTILL_API_KEY or "unused",
            base_url=DISTILL_BASE_URL,
            timeout=45.0,
            max_retries=0,  # we do our own transient retry with backoff
        )
    return _distill_client


def _extract_json(text: str):
    """Defensive JSON extraction (pattern proven in eval/eval_realqueries.py):
    direct parse → outermost {...} span → per-block regex."""
    if not text:
        return None
    text = text.strip()
    # strip code fences
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass
    import re
    for m in reversed(re.findall(r"\{[^{}]*\}", text, re.S)):
        try:
            return json.loads(m)
        except Exception:
            continue
    return None


_DISTILL_SYSTEM = """You distill retrieval packets from the operator's knowledge graph into direct answers.

You will get a QUESTION and a RETRIEVAL PACKET (JSON with facts, communities, entities, a source-note excerpt, and semantic note hits). Rules:

1. Answer ONLY from the packet. NEVER use your own knowledge or guess. If the packet does not contain the answer, return confidence "not_found" with answer null and escalate true.
2. Respect the `current` flag on facts. `current: false` = superseded/outdated — never present it as the current truth. If a superseded value is relevant history, mention it ONLY in `superseded_note` (e.g. "was X until <date>").
3. Be direct and terse: the answer is the value/fact itself plus a few words of essential context. No preamble, no hedging, no restating the question. When a durable claim in the packet answers the question verbatim, quote it.
4. `sources`: list the source-note slugs (e.g. "2026-06-30-174108-ingress-vip-fix") of the packet items you actually used. Empty list only when not_found.
5. `confidence`: "high" = a current fact or excerpt states it directly; "medium" = inferred by combining packet items; "low" = weak/indirect support; "not_found" = packet lacks it.
6. `escalate`: true when the caller should fall back to full graph_search (not_found, or the question needs broad context the packet lacks). Otherwise false.

Reply with ONLY a JSON object:
{"answer": "..." | null, "confidence": "high|medium|low|not_found", "sources": ["slug", ...], "superseded_note": "..." (omit if none), "escalate": true|false}"""

_TRANSIENT_MARKERS = ("connection", "timeout", "timed out", "429", "rate limit", "ratelimit", "overloaded", "502", "503")


async def _distill(question: str, packet_json: str) -> dict | None:
    """One LLM call to distill the packet. Returns parsed dict or None on
    failure (caller degrades gracefully — never a 500)."""
    client = _get_distill_client()
    last: Exception | None = None
    model = DISTILL_MODEL
    for attempt in range(3):
        try:
            resp = await client.chat.completions.create(
                model=model,
                temperature=0,
                max_tokens=400,
                messages=[
                    {"role": "system", "content": _DISTILL_SYSTEM},
                    {"role": "user", "content": f"QUESTION: {question}\n\nRETRIEVAL PACKET (JSON):\n{packet_json}\n\nReply with ONLY the JSON object."},
                ],
            )
            content = (resp.choices[0].message.content or "") if resp.choices else ""
            parsed = _extract_json(content)
            if isinstance(parsed, dict) and "confidence" in parsed:
                return parsed
            last = ValueError(f"unparseable distiller output: {content[:200]!r}")
        except Exception as e:  # noqa: BLE001
            last = e
            msg = f"{type(e).__name__} {e}".lower()
            rate_limited = any(t in msg for t in ("429", "rate limit", "ratelimit", "quota"))
            if rate_limited and DISTILL_FALLBACK_MODEL and model != DISTILL_FALLBACK_MODEL:
                # Primary model's quota window blew — switch to the fallback
                # model instead of hammering or erroring out.
                log.warning("distiller: %s rate-limited, falling back to %s", model, DISTILL_FALLBACK_MODEL)
                model = DISTILL_FALLBACK_MODEL
            elif not any(t in msg for t in _TRANSIENT_MARKERS):
                break  # non-transient — don't hammer
        await asyncio.sleep(min(2 ** attempt, 8))
    log.warning("distiller failed: %r", last)
    return None


# --- MCP tools (native Graphiti surface) ---
mcp = FastMCP("graphiti-mcp", stateless_http=True, host=HOST, port=PORT)


async def _graph_answer_impl(question: str, is_test: bool = False) -> dict:
    """Shared implementation for the MCP tool and the /api/answer route.
    `is_test` tags the metrics event so smoke tests don't pollute the weekly
    not_found rate / gap candidates."""
    t0 = time.time()
    q = (question or "").strip()
    # normalize: lowercase, collapse whitespace, strip trailing punctuation
    # ("...annotation" and "...Annotation?" must share a cache entry — observed miss on day one)
    key = " ".join(q.lower().split()).rstrip("?!. ")

    if ANSWER_CACHE_TTL > 0:
        hit = _ANSWER_CACHE.get(key)
        if hit and hit[0] > time.time():
            result = dict(hit[1])
            event = {
                "ts": time.time(), "at": _now_iso(), "source": "mcp", "tool": "graph_answer",
                "question": q[:200], "confidence": result["confidence"],
                "n_sources": len(result.get("sources") or []), "escalate": result["escalate"],
                "answer_tokens_est": len(result.get("answer") or "") // 4,
                "packet_tokens_est": hit[2], "assemble_ms": 0, "distill_ms": 0,
                "cached": True,
            }
            if is_test:
                event["test"] = True
            _record_query(event)
            return result
        if hit:
            _ANSWER_CACHE.pop(key, None)  # expired

    packet: dict | None = None
    try:
        # Full-fat packet internally (k=8, untrimmed excerpt) — the trim in
        # graph_search applies to the wire, not the distiller's input.
        packet = await _assemble_packet(q, 8, excerpt_chars=2500, n_communities=3, community_chars=1000)
    except Exception as e:  # noqa: BLE001
        log.warning("graph_answer: packet assembly failed: %r", e)
    assemble_ms = int((time.time() - t0) * 1000)
    t1 = time.time()

    result: dict
    packet_tokens_est = 0
    if packet is None:
        result = {"answer": None, "confidence": "error", "sources": [], "escalate": True}
    else:
        packet_json = json.dumps(packet, ensure_ascii=False)
        packet_tokens_est = len(packet_json) // 4
        parsed = await _distill(q, packet_json)
        if parsed is None:
            # Distiller down/unparseable → degrade gracefully; agent falls
            # back to graph_search. NEVER 500.
            result = {"answer": None, "confidence": "error", "sources": [], "escalate": True}
        else:
            conf = str(parsed.get("confidence", "low")).strip().lower()
            if conf not in ("high", "medium", "low", "not_found"):
                conf = "low"
            answer = parsed.get("answer")
            answer = None if answer in (None, "", "null") else str(answer)[:2000]
            if conf == "not_found":
                answer = None
            srcs = parsed.get("sources") or []
            if not isinstance(srcs, list):
                srcs = [srcs]
            sources = [str(s).strip() for s in srcs if s and str(s).strip()][:5]
            escalate = bool(parsed.get("escalate", False)) or conf == "not_found" or answer is None
            result = {"answer": answer, "confidence": conf, "sources": sources, "escalate": escalate}
            note = parsed.get("superseded_note")
            if note and str(note).strip().lower() not in ("none", "null", "n/a"):
                result["superseded_note"] = str(note)[:500]

    distill_ms = int((time.time() - t1) * 1000)

    # Cache real answers only (not_found/error always re-run — the memory may
    # gain the answer, and errors are transient).
    if ANSWER_CACHE_TTL > 0 and result.get("answer") and result["confidence"] in ("high", "medium", "low"):
        if len(_ANSWER_CACHE) >= _ANSWER_CACHE_MAX:
            oldest = min(_ANSWER_CACHE, key=lambda c: _ANSWER_CACHE[c][0])
            _ANSWER_CACHE.pop(oldest, None)
        _ANSWER_CACHE[key] = (time.time() + ANSWER_CACHE_TTL, dict(result), packet_tokens_est)

    event = {
        "ts": time.time(), "at": _now_iso(), "source": "mcp", "tool": "graph_answer",
        "question": q[:200], "confidence": result["confidence"],
        "n_sources": len(result.get("sources") or []), "escalate": result["escalate"],
        "answer_tokens_est": len(result.get("answer") or "") // 4,
        "packet_tokens_est": packet_tokens_est,
        "assemble_ms": assemble_ms,
        "distill_ms": distill_ms,
    }
    if is_test:
        event["test"] = True
    _record_query(event)
    return result


@mcp.tool()
async def graph_answer(question: str) -> dict:
    """Ask the operator's memory a question and get a DIRECT ANSWER (not search
    results). Returns {answer, confidence, sources, superseded_note?, escalate}.
    Use this FIRST for any factual question about the operator's stack, deploys,
    services, decisions, conventions. Escalate to graph_search only when you
    need broad context, not an answer (or when this returns escalate=true)."""
    return await _graph_answer_impl(question)


@mcp.tool()
async def graph_search(query: str, k: int = 5) -> dict:
    """Search the operator's Graphiti knowledge graph for `query`. Returns a structured
    packet (NOT a list of pages): the top atomic FACTS (each with its source-note
    slug + validity window — `current=false` means superseded), a relevant
    COMMUNITY summary (auto-synthesized from notes), the top ENTITIES, and an
    excerpt of the top SOURCE NOTE. This is the native Graphiti retrieval.

    For a factual question, prefer graph_answer (direct distilled answer).
    Use this for broad/exploratory context, or when graph_answer escalates."""
    kk = max(1, min(int(k), 25))
    packet = await _assemble_packet(query, kk)
    _record_query({
        "ts": time.time(), "at": _now_iso(), "source": "mcp", "tool": "graph_search",
        "query": (query or "")[:200], "k": kk,
        "n_facts": len(packet["facts"]), "n_communities": len(packet["communities"]),
        "n_entities": len(packet["entities"]), "has_source": packet["source_excerpt"] is not None,
        "n_semantic": len(packet["semantic_notes"]),
    })
    return packet


@mcp.tool()
async def graph_get_note(slug: str) -> dict | None:
    """Fetch a source note's full content by its timestamp slug (e.g.
    `2026-05-08-101301-cli-self-hosted-quirks`). Every fact in the graph
    traces to exactly one source note — this is the provenance fetch. Returns
    {slug, content, valid_at} or None if not ingested."""
    g = await _get_graph()
    rows, _, _ = await g.driver.execute_query(
        "MATCH (e:Episodic {name: $slug}) RETURN e.content AS content, e.valid_at AS valid_at LIMIT 1",
        slug=slug,
    )
    if not rows:
        _record_query({"ts": time.time(), "at": _now_iso(), "source": "mcp", "tool": "graph_get_note", "slug": slug, "hit": False})
        return None
    r = rows[0]
    _record_query({"ts": time.time(), "at": _now_iso(), "source": "mcp", "tool": "graph_get_note", "slug": slug, "hit": True, "chars": len(r.get("content") or "")})
    return {"slug": slug, "content": r.get("content") or "", "valid_at": str(r.get("valid_at") or "")}


@mcp.tool()
async def graph_entity(name: str) -> dict | None:
    """Look up a known ENTITY by name and return its summary + its CURRENT facts
    (superseded facts excluded) + attributes. Use this when you already know the
    thing (e.g. a service, tool, decision) and want its current state and related
    facts — the bitemporal angle wiki_search can't provide."""
    g = await _get_graph()
    rows, _, _ = await g.driver.execute_query(
        "MATCH (n:Entity) WHERE toLower(n.name) = toLower($name) "
        "RETURN n.name AS name, n.summary AS summary, n.group_id AS group_id LIMIT 1",
        name=name,
    )
    if not rows:
        _record_query({"ts": time.time(), "at": _now_iso(), "source": "mcp", "tool": "graph_entity", "name": name, "hit": False})
        return None
    n = rows[0]
    # current facts touching this entity (invalid_at null = still current)
    frows, _, _ = await g.driver.execute_query(
        "MATCH (n:Entity)-[r]-(m:Entity) WHERE toLower(n.name) = toLower($name) "
        "AND r.fact IS NOT NULL AND r.invalid_at IS NULL "
        "RETURN r.fact AS fact, m.name AS other, r.valid_at AS valid_at "
        "ORDER BY r.valid_at DESC LIMIT 25",
        name=name,
    )
    facts = [{"fact": f["fact"], "other": f["other"], "valid_at": str(f["valid_at"] or "")} for f in frows]
    _record_query({"ts": time.time(), "at": _now_iso(), "source": "mcp", "tool": "graph_entity", "name": name, "hit": True, "n_facts": len(facts)})
    return {"name": n["name"], "summary": n.get("summary") or "", "current_facts": facts}


@mcp.tool()
async def graph_current_facts(subject: str) -> list[dict]:
    """Return the CURRENT atomic facts about a subject (free-text). Excludes
    superseded/outdated facts (invalid_at set). Use this when you specifically
    need what's true NOW about something — the temporal query wiki_search can't
    answer (it returns documents regardless of recency)."""
    g = await _get_graph()
    res = await g.search_(subject, config=COMBINED_HYBRID_SEARCH_RRF.model_copy(update={"limit": 15}))
    slug_map = await _resolve_episode_slugs(g, list(res.edges or []))
    out = []
    for e in (res.edges or []):
        if getattr(e, "invalid_at", None):  # skip superseded
            continue
        out.append({
            "fact": getattr(e, "fact", "") or str(e),
            "source_slug": _episode_slug(e, slug_map),
            "valid_at": str(getattr(e, "valid_at", "") or ""),
        })
        if len(out) >= 10:
            break
    _record_query({"ts": time.time(), "at": _now_iso(), "source": "mcp", "tool": "graph_current_facts", "subject": (subject or "")[:200], "n": len(out)})
    return out


@mcp.tool()
async def graph_changes(subject: str, since_days: int = 14) -> dict:
    """What CHANGED about a subject recently — the temporal diff. Returns facts
    that became true (new) and facts that were superseded (invalidated) within
    the window. Perfect when resuming work on a project after time away:
    one call instead of five searches. `subject` is matched against entity
    names and fact text."""
    days = max(1, min(int(since_days), 120))
    g = await _get_graph()
    new_rows, _, _ = await g.driver.execute_query(
        "MATCH (a:Entity)-[r:RELATES_TO]-(b:Entity) "
        "WHERE r.fact IS NOT NULL AND r.valid_at >= datetime() - duration({days: $days}) "
        "AND (toLower(a.name) CONTAINS toLower($s) OR toLower(b.name) CONTAINS toLower($s) "
        "     OR toLower(r.fact) CONTAINS toLower($s)) "
        "RETURN DISTINCT r.fact AS fact, toString(r.valid_at) AS valid_at, "
        "       r.invalid_at IS NULL AS current "
        "ORDER BY valid_at DESC LIMIT 25",
        s=subject, days=days,
    )
    superseded_rows, _, _ = await g.driver.execute_query(
        "MATCH (a:Entity)-[r:RELATES_TO]-(b:Entity) "
        "WHERE r.fact IS NOT NULL AND r.invalid_at IS NOT NULL "
        "AND r.invalid_at >= datetime() - duration({days: $days}) "
        "AND (toLower(a.name) CONTAINS toLower($s) OR toLower(b.name) CONTAINS toLower($s) "
        "     OR toLower(r.fact) CONTAINS toLower($s)) "
        "RETURN DISTINCT r.fact AS fact, toString(r.valid_at) AS valid_at, "
        "       toString(r.invalid_at) AS invalid_at "
        "ORDER BY invalid_at DESC LIMIT 15",
        s=subject, days=days,
    )
    out = {
        "subject": subject,
        "window_days": days,
        "new_facts": [{"fact": r["fact"], "valid_at": r["valid_at"], "current": r["current"]}
                      for r in new_rows],
        "superseded": [{"fact": r["fact"], "was_valid_from": r["valid_at"],
                        "superseded_at": r["invalid_at"]} for r in superseded_rows],
    }
    _record_query({
        "ts": time.time(), "at": _now_iso(), "source": "mcp", "tool": "graph_changes",
        "subject": (subject or "")[:200], "days": days,
        "n_new": len(out["new_facts"]), "n_superseded": len(out["superseded"]),
    })
    return out


# --- Plain HTTP routes (parity with wiki-mcp /api/* for non-MCP clients) ---
async def _http_graph_search(req: Request) -> JSONResponse:
    q = req.query_params.get("q", "").strip()
    if not q:
        return JSONResponse({"error": "missing q"}, status_code=400)
    k = max(1, min(int(req.query_params.get("k", "5")), 25))
    if req.query_params.get("test", "").lower() in ("1", "true", "yes"):
        # healthcheck/smoke probes: serve the packet but tag the metrics event
        # so usage stats and the weekly gaps report stay clean.
        packet = await _assemble_packet(q, k)
        _record_query({
            "ts": time.time(), "at": _now_iso(), "source": "mcp", "tool": "graph_search",
            "query": q[:200], "k": k, "n_facts": len(packet["facts"]),
            "n_communities": len(packet["communities"]), "n_entities": len(packet["entities"]),
            "has_source": packet["source_excerpt"] is not None,
            "n_semantic": len(packet["semantic_notes"]), "test": True,
        })
        return JSONResponse(packet)
    return JSONResponse(await graph_search(q, k))


async def _http_graph_answer(req: Request) -> JSONResponse:
    q = req.query_params.get("q", "").strip()
    if not q:
        return JSONResponse({"error": "missing q"}, status_code=400)
    # ?test=1 tags the metrics event so smoke tests don't pollute weekly stats.
    is_test = req.query_params.get("test", "").lower() in ("1", "true", "yes")
    return JSONResponse(await _graph_answer_impl(q, is_test=is_test))


async def _http_health(_req: Request) -> JSONResponse:
    return JSONResponse({"ok": _g is not None, "metrics_path": str(METRICS_PATH) if METRICS_PATH else None})


async def _http_metrics(req: Request) -> JSONResponse:
    """Tail of the query log (for the weekly gap-miner). ?days=7 filters by ts."""
    days = float(req.query_params.get("days", "7"))
    cutoff = time.time() - days * 86400
    events = []
    if METRICS_PATH and METRICS_PATH.exists():
        try:
            with METRICS_PATH.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                        if e.get("ts", 0) >= cutoff:
                            events.append(e)
                    except Exception:
                        continue
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"events": events[-5000:], "days": days})


http_routes = [
    Route("/health", _http_health),
    Route("/api/search", _http_graph_search),
    Route("/api/answer", _http_graph_answer),
    Route("/api/metrics", _http_metrics),
]

mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(_app):
    # Warm the Graphiti client at startup so the first query isn't slow.
    try:
        await _get_graph()
        log.info("graphiti client ready")
    except Exception as e:
        log.error("failed to warm graphiti client at startup: %s", e)
    async with mcp_app.router.lifespan_context(mcp_app):
        yield
    if _g is not None:
        try:
            await _g.close()
        except Exception:
            pass


app = Starlette(
    routes=http_routes + [Mount("/", app=mcp_app)],
    lifespan=lifespan,
)


def main():
    import uvicorn
    log.info("starting graphiti-mcp on %s:%d (metrics=%s)", HOST, PORT, METRICS_PATH)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
