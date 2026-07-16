"""
dip.ink graph ingest + query.

Ingests wiki source notes into a Graphiti/Neo4j temporal knowledge graph.
Extraction runs against any OpenAI-compatible chat endpoint (OpenAI itself,
LiteLLM, a local proxy, ...); embeddings use OpenAI text-embedding-3-small.
Each note becomes one episode; episode name = note slug, reference_time is
parsed from the slug's YYYY-MM-DD-HHMMSS prefix so the bitemporal timeline is
real and every extracted fact traces to its source note.

Usage:
    INGEST_MODE=cron    python ingest.py   # resumable bounded batch (the scheduler mode)
    INGEST_MODE=status  python ingest.py   # done/pending/total counts, non-mutating
    INGEST_MODE=ingest  python ingest.py   # one-shot: ingest NOTE_LIMIT notes
    INGEST_MODE=query   python ingest.py   # run a few canned searches
    INGEST_MODE=both    python ingest.py   # ingest then query
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from graphiti_core import Graphiti
from graphiti_core.llm_client import LLMConfig
from graphiti_core.llm_client.client import get_extraction_language_instruction
# OpenAIGenericClient is not re-exported from the llm_client package __init__
# (only OpenAIClient is), so import it from its module directly.
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.llm_client.config import DEFAULT_MAX_TOKENS, ModelSize
from graphiti_core.nodes import EpisodeType


def patch_community_clustering() -> None:
    """Monkeypatch graphiti's community-clustering to fix two upstream defects:

    1. ``get_community_clusters`` runs ONE cypher query PER ENTITY (959 sequential
       round-trips for a 959-entity graph = ~7.5 min). Replace with a single bulk
       query that returns the whole adjacency projection in <1s.
    2. ``label_propagation`` is ``while True:`` with NO iteration cap, and it
       OSCILLATES on this graph (never converges) -> infinite loop, the actual
       build_communities wedge. Add a max-iteration cap with last-state-as-answer.

    Both overrides live in the graphiti community_operations module namespace so
    Graphiti.build_communities() picks them up. Idempotent.
    """
    from collections import defaultdict
    from graphiti_core.utils.maintenance import community_operations as co
    from graphiti_core.utils.maintenance.community_operations import Neighbor

    async def fast_get_community_clusters(driver, group_ids):
        """Bulk-query the whole RELATES_TO adjacency in ONE cypher, then cluster."""
        if group_ids is None:
            rows, _, _ = await driver.execute_query(
                "MATCH (n:Entity) WHERE n.group_id IS NOT NULL "
                "RETURN collect(DISTINCT n.group_id) AS gids"
            )
            group_ids = rows[0]["gids"] if rows else []

        all_clusters: list[list] = []
        for gid in group_ids:
            # ONE query: every (n)-[RELATES_TO]-(m) pair in this group, with counts.
            rows, _, _ = await driver.execute_query(
                "MATCH (n:Entity {group_id: $gid})-[e:RELATES_TO]-(m:Entity {group_id: $gid}) "
                "WITH n.uuid AS src, m.uuid AS tgt, count(e) AS cnt "
                "RETURN collect([src, tgt, cnt]) AS edges",
                gid=gid,
            )
            projection: dict[str, list[Neighbor]] = defaultdict(list)
            for src, tgt, cnt in (rows[0]["edges"] if rows else []):
                projection[src].append(Neighbor(node_uuid=tgt, edge_count=cnt))
            # entities with no edges still need to be their own singleton community
            ent_rows, _, _ = await driver.execute_query(
                "MATCH (n:Entity {group_id: $gid}) RETURN collect(n.uuid) AS uuids", gid=gid
            )
            for uuid in (ent_rows[0]["uuids"] if ent_rows else []):
                projection.setdefault(uuid, [])

            cluster_uuids = co.label_propagation(dict(projection))  # capped below
            # hydrate clusters to EntityNode objects (matches original semantics)
            hydrated = await asyncio.gather(
                *[co.EntityNode.get_by_uuids(driver, c) for c in cluster_uuids]
            )
            all_clusters.extend(hydrated)
        return all_clusters

    def capped_label_propagation(projection, max_iters: int = 50):
        """label_propagation with an iteration cap. Original oscillates forever on
        some graphs; we stop at max_iters and return the current assignment."""
        community_map = {uuid: i for i, uuid in enumerate(projection.keys())}
        for _ in range(max_iters):
            no_change = True
            new_cm: dict[str, int] = {}
            for uuid, neighbors in projection.items():
                curr = community_map[uuid]
                cands: dict[int, int] = defaultdict(int)
                for nb in neighbors:
                    cands[community_map[nb.node_uuid]] += nb.edge_count
                lst = sorted(((c, com) for com, c in cands.items()), reverse=True)
                rank, cand = lst[0] if lst else (0, -1)
                new_com = cand if (cand != -1 and rank > 1) else max(cand, curr)
                new_cm[uuid] = new_com
                if new_com != curr:
                    no_change = False
            community_map = new_cm
            if no_change:
                break
        ccm = defaultdict(list)
        for uuid, com in community_map.items():
            ccm[com].append(uuid)
        return list(ccm.values())

    co.get_community_clusters = fast_get_community_clusters
    co.label_propagation = capped_label_propagation
    co.MAX_COMMUNITY_BUILD_CONCURRENCY = 2

    # build_community's tree-reduction calls semaphore_gather(summarize_pair(...))
    # with NO max_coroutines, so it uses the global SEMAPHORE_LIMIT (>=10). For a
    # 252-member cluster that fires ~125 parallel LLM calls in one tree level ->
    # LLM APITimeoutError/RateLimitError. Override build_community to bound every
    # inner gather to COMMUNITY_LLM_CONCURRENCY (matches our 2-key budget).
    COMMUNITY_LLM_CONCURRENCY = 2
    from graphiti_core.utils.maintenance.community_operations import (
        build_community_edges, generate_summary_description, summarize_pair,
        truncate_at_sentence, MAX_SUMMARY_CHARS,
    )
    from graphiti_core.utils.datetime_utils import utc_now
    from graphiti_core.helpers import semaphore_gather
    from graphiti_core.nodes import CommunityNode

    async def bounded_build_community(llm_client, community_cluster):
        summaries = [e.summary for e in community_cluster]
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
                max_coroutines=COMMUNITY_LLM_CONCURRENCY,
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

    co.build_community = bounded_build_community
    print("[patch] community clustering patched (bulk proj + iter cap + conc=2 + bounded tree)", flush=True)


# Apply the patch at import time so any caller of build_communities gets the fix.
patch_community_clustering()


def _example_shape(node, defs):
    """Reduce a JSON-Schema fragment to a concrete example object with placeholder
    values (objects->dict, arrays->[item], string->'<string>', enum->first value).
    Used so we can inject a COMPACT shape into the prompt instead of the verbose
    ``model_json_schema()`` (which some models echo back verbatim instead of filling in)."""
    if not isinstance(node, dict):
        return "<value>"
    if "$ref" in node:  # e.g. "#/$defs/Foo"
        return _example_shape(defs.get(node["$ref"].split("/")[-1], {}), defs)
    for key in ("anyOf", "oneOf"):
        if key in node:  # pick the first non-null option
            for opt in node[key]:
                if opt.get("type") != "null":
                    return _example_shape(opt, defs)
    t = node.get("type")
    if t == "object" or "properties" in node:
        props = node.get("properties", {})
        out = {k: _example_shape(v, defs) for k, v in props.items()}
        return out or "<object>"
    if t == "array":
        return [_example_shape(node.get("items", {}), defs)]
    if t == "string":
        enum = node.get("enum")
        return enum[0] if enum else "<string>"
    if t in ("integer", "number"):
        return 0
    if t == "boolean":
        return False
    return "<value>"


class CompactSchemaClient(OpenAIGenericClient):
    """OpenAIGenericClient with two robustness fixes for non-OpenAI models
    (developed against GLM via LiteLLM; harmless for models that don't need them):

    1. **Compact example shape instead of the verbose JSON Schema.** graphiti's
       ``json_object`` mode injects ``model_json_schema()`` (with
       ``$defs``/``$ref``/``properties``) into the prompt; some models mimic
       that template verbatim instead of producing an instance. We override
       ``generate_response`` to inject a COMPACT example shape instead
       (resolved from the schema).

    2. A defensive ``{"answer": ...}`` envelope unwrap — the residual wrapper
       some models occasionally add around the requested object.

    Note: if your model does extended thinking by default, disable it at the
    proxy/model level (thinking eats max_tokens as reasoning text so the JSON
    never lands).
    """

    async def generate_response(
        self,
        messages,
        response_model=None,
        max_tokens=None,
        model_size: ModelSize = ModelSize.medium,
        group_id=None,
        prompt_name=None,
        *,
        attribute_extraction: bool = False,
    ):
        # Faithful reproduction of OpenAIGenericClient.generate_response, with the
        # ONE change: inject a compact example shape instead of the verbose schema.
        self._apply_attribute_extraction_preamble(messages, attribute_extraction)
        if max_tokens is None:
            max_tokens = self.max_tokens
        if response_model is not None and self.structured_output_mode == "json_object":
            schema = response_model.model_json_schema()
            example = json.dumps(_example_shape(schema, schema.get("$defs", {})))
            messages[
                -1
            ].content += (
                "\n\nRespond with ONLY a JSON object matching this example shape "
                "(fill in real values, keep the same keys and nesting):\n\n"
                + example
            )
        messages[0].content += get_extraction_language_instruction(group_id)
        with self.tracer.start_span("llm.generate") as span:
            span.add_attributes(
                {
                    "llm.provider": "openai",
                    "model.size": model_size.value,
                    "max_tokens": max_tokens,
                    **({"prompt.name": prompt_name} if prompt_name else {}),
                }
            )
            try:
                return await self._generate_response_with_retry(
                    messages, response_model, max_tokens=max_tokens, model_size=model_size
                )
            except Exception as e:
                span.set_status("error", str(e))
                span.record_exception(e)
                raise

    async def _generate_response(
        self,
        messages,
        response_model=None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        model_size: ModelSize = ModelSize.medium,
    ):
        result = await super()._generate_response(messages, response_model, max_tokens, model_size)
        # Defensive: peel a residual {"answer": {...}} envelope (bounded).
        for _ in range(3):
            if isinstance(result, dict) and len(result) == 1 and "answer" in result:
                inner = result["answer"]
                if isinstance(inner, dict):
                    result = inner
                    continue
            break
        if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
            result = result[0]
        return result

# ---------------------------------------------------------------------------
# Configuration (all from env)
# ---------------------------------------------------------------------------
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]

# Extraction LLM: any OpenAI-compatible chat endpoint. Leave LLM_BASE_URL
# unset to use OpenAI directly (OPENAI_API_KEY + LLM_MODEL, e.g. gpt-4.1-mini);
# point it at a LiteLLM/other proxy to use a different model for extraction.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "").strip()
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL") or "gpt-4.1-mini"

NOTES_ROOT = Path(os.environ.get("NOTES_ROOT", "/notes/wiki/sources/notes"))
# Also ingest UN-curated inbox notes. Graphiti doesn't need the curation step —
# raw notes are its native input — so we walk the inbox roots directly. rglob
# on the inbox root catches both the active inbox and the nested .deferred/
# holding pen. Colon-separated; set INBOX_ROOTS="" for canonical-only behavior.
INBOX_ROOTS = [Path(p) for p in os.environ.get("INBOX_ROOTS", "/notes/notes").split(":") if p]
NOTE_LIMIT = int(os.environ.get("NOTE_LIMIT", "100"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "1"))  # MUST be 1: graphiti's
# add_episode does non-atomic read-modify-write of edge invalidation; concurrent
# same-entity writes lose ALL invalidations (proven empirically — 5 contradictory
# facts all left 'current'). Notes sort oldest-first so a project's notes burst
# into the same window. Serial is correct; throughput is not the constraint
# (steady-state is 1-2 notes/tick).

# YYYY-MM-DD-HHMMSS-slug  (source-note naming convention, UTC timestamps)
SLUG_TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})-")

# LLM providers are intermittently flaky under sustained extraction load
# (APIConnectionError / APITimeoutError on the heavier prompts, even when simple
# probes return 200). Retry the whole add_episode on transient errors so a blip
# doesn't fail an otherwise-fine note. Non-transient errors (schema, etc.) raise
# immediately. Bounded so a genuinely-down upstream still trips the circuit breaker.
ADD_EPISODE_RETRIES = int(os.environ.get("ADD_EPISODE_RETRIES", "4"))
_TRANSIENT = (
    "APIConnectionError", "APITimeoutError", "APIStatusError",
    "RateLimitError", "RouterRateLimitError", "InternalServerError",
)


async def add_episode_with_retry(g, **kw):
    """g.add_episode with retry-on-transient (connection/timeout/rate-limit)."""
    last = None
    for attempt in range(ADD_EPISODE_RETRIES + 1):
        try:
            return await g.add_episode(**kw)
        except Exception as e:  # noqa: BLE001
            last = e
            msg, ename = str(e), type(e).__name__
            transient = (
                ename in _TRANSIENT
                or "Connection" in msg or "connection" in msg
                or "timeout" in msg.lower() or "timed out" in msg.lower()
                or "429" in msg
            )
            if not transient or attempt == ADD_EPISODE_RETRIES:
                raise
            wait = min(2 ** attempt, 20)
            print(f"[ingest] transient {ename} (retry {attempt + 1}/{ADD_EPISODE_RETRIES} in {wait}s): {msg[:70]}",
                  file=sys.stderr)
            await asyncio.sleep(wait)
    raise last  # unreachable


def _with_roomy_pool(g: Graphiti) -> Graphiti:
    """Replace graphiti's neo4j driver with one carrying a larger connection pool.

    The default `max_connection_pool_size=100` (acquisition timeout 60s) gets
    exhausted by graphiti's concurrent gathers on the full corpus —
    ConnectionAcquisitionTimeoutError mid-build_communities. graphiti's
    Neo4jDriver creates its client with no pool config, so we swap in a driver
    with a 500-connection pool + 120s acquisition timeout.
    """
    from neo4j import AsyncGraphDatabase
    drv = AsyncGraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
        max_connection_pool_size=int(os.environ.get("NEO4J_MAX_POOL", "500")),
        connection_acquisition_timeout=int(os.environ.get("NEO4J_ACQ_TIMEOUT", "120")),
    )
    g.driver.client = drv
    if getattr(g, "clients", None) is not None:
        g.clients.driver = g.driver  # keep the (graphiti) GraphDriver wrapper, swap its neo4j client
    return g


def build_graphiti() -> Graphiti:
    """Wire Graphiti: extraction LLM from env, default OpenAI embedder.

    - LLM_BASE_URL unset → graphiti's default OpenAIClient (reads
      OPENAI_API_KEY), model = LLM_MODEL. The simplest one-key setup.
    - LLM_BASE_URL set → CompactSchemaClient against that OpenAI-compatible
      endpoint with LLM_API_KEY/LLM_MODEL, json_object mode (works with
      models that don't honor json_schema constrained decoding).
    """
    if LLM_BASE_URL:
        llm_config = LLMConfig(
            api_key=LLM_API_KEY,
            model=LLM_MODEL,
            base_url=LLM_BASE_URL,
        )
        llm_client = CompactSchemaClient(
            config=llm_config,
            structured_output_mode="json_object",
        )
    else:
        from graphiti_core.llm_client import OpenAIClient
        llm_client = OpenAIClient(config=LLMConfig(api_key=LLM_API_KEY, model=LLM_MODEL))
    # embedder=None -> graphiti defaults to OpenAIEmbedder, which reads
    # OPENAI_API_KEY from env.
    g = Graphiti(
        uri=NEO4J_URI,
        user=NEO4J_USER,
        password=NEO4J_PASSWORD,
        llm_client=llm_client,
        embedder=None,
    )
    return _with_roomy_pool(g)


DEFAULT_GROUP_ID = os.environ.get("GROUP_ID", "main")


def build_graphiti_on_group(group_id: str = DEFAULT_GROUP_ID) -> Graphiti:
    """Same as build_graphiti but with the driver cloned to the group's database.

    Notes are ingested with group_id set (so communities can form and queries
    scope). Any read-side caller — search, build_communities, eval — must point
    at the same database or it sees an empty graph.
    """
    g = build_graphiti()
    g.driver = g.driver.clone(database=group_id)
    g.clients.driver = g.driver
    return _with_roomy_pool(g)  # clone rebuilt the neo4j client with default pool


def discover_notes() -> list[tuple[datetime, str, Path]]:
    """Walk NOTES_ROOT + INBOX_ROOTS, return [(reference_time, slug, path)] sorted
    ascending (oldest first — required for correct bitemporal supersession).

    Each source note lives at .../<slug>/<slug>.md where the canonical file's
    name equals its parent folder name. reference_time is parsed from the slug's
    YYYY-MM-DD-HHMMSS prefix, so a backfilled note lands at its ORIGINAL
    event-time (not "now") and Graphiti's valid_at-based invalidation orders it
    correctly. Deduped by slug: the canonical wiki/sources/notes/ copy (walked
    first) wins over an inbox copy of the same slug.
    """
    out: list[tuple[datetime, str, Path]] = []
    seen: set[str] = set()
    for root in [NOTES_ROOT, *INBOX_ROOTS]:
        if not root.exists():
            continue
        for md in root.rglob("*.md"):
            slug = md.stem
            # canonical source-note file: filename == parent folder name
            if md.parent.name != slug:
                continue
            m = SLUG_TS_RE.match(slug)
            if not m:
                continue
            if slug in seen:  # dedup: first root (canonical) wins
                continue
            seen.add(slug)
            ts = datetime(*(int(x) for x in m.groups()), tzinfo=timezone.utc)
            out.append((ts, slug, md))
    out.sort(key=lambda t: t[0])
    return out


async def ingest() -> None:
    notes = discover_notes()
    total = len(notes)
    print(f"[ingest] discovered {total} canonical source notes under {NOTES_ROOT}")
    if total == 0:
        print("[ingest] nothing to ingest; check NOTES_ROOT / git clone", file=sys.stderr)
        return
    selected = notes[:NOTE_LIMIT]
    print(
        f"[ingest] ingesting {len(selected)} of {total} "
        f"({selected[0][0].isoformat()} → {selected[-1][0].isoformat()} UTC)"
    )

    g = build_graphiti()
    # When ingesting with a group_id, add_episode clones the driver to a
    # database named after the group. Build indices on THAT database by cloning
    # up front — otherwise build_indices_and_constraints runs on the default db
    # and the group db has none (queries return nothing / communities can't form
    # because get_community_clusters needs indexed RELATES_TO edges + group_id).
    GROUP_ID = DEFAULT_GROUP_ID
    g.driver = g.driver.clone(database=GROUP_ID)
    g.clients.driver = g.driver
    try:
        print(f"[ingest] building indices and constraints on db '{GROUP_ID}'...")
        await g.build_indices_and_constraints()

        sem = asyncio.Semaphore(CONCURRENCY)
        done = 0
        failed: list[str] = []

        async def one(ts: datetime, slug: str, path: Path) -> None:
            nonlocal done
            body = path.read_text(encoding="utf-8", errors="replace")
            async with sem:
                try:
                    await add_episode_with_retry(
                        g,
                        name=slug,  # provenance: every fact → this source note
                        episode_body=body,
                        source=EpisodeType.text,
                        source_description="wiki source note",
                        reference_time=ts,  # bitemporal valid_time
                        group_id=GROUP_ID,  # set so communities can form + queries scope
                    )
                    done += 1
                    print(f"[ingest] ok ({done}/{len(selected)}) {slug}")
                except Exception as e:  # one note failing must not kill the run
                    failed.append(slug)
                    print(f"[ingest] FAIL {slug}: {e!r}", file=sys.stderr)

        await asyncio.gather(*(one(ts, slug, p) for ts, slug, p in selected))
        print(
            f"[ingest] done: {done}/{len(selected)} ok, {len(failed)} failed. "
            f"Failures: {failed[:10]}{'…' if len(failed) > 10 else ''}"
        )
    finally:
        await g.close()


async def _get_done_slugs(driver) -> set[str]:
    """Slugs of episodes that are fully ingested: episode node exists AND has
    >=1 entity edge (so it's not a half-written crash victim)."""
    rows, _, _ = await driver.execute_query(
        "MATCH (e:Episodic)-[:MENTIONS|RELATES_TO]->(:Entity) "
        "WHERE e.group_id IS NOT NULL OR e.group_id IS NULL "  # match all
        "RETURN collect(DISTINCT e.name) AS done"
    )
    return set(rows[0]["done"]) if rows else set()


async def _get_partial_slugs(driver) -> set[str]:
    """Episode nodes with ZERO entity edges — a previous crash mid-add_episode.
    Cleaned before each batch so they re-ingest cleanly."""
    rows, _, _ = await driver.execute_query(
        "MATCH (e:Episodic) WHERE NOT (e)-[:MENTIONS|RELATES_TO]->(:Entity) "
        "RETURN collect(DISTINCT e.name) AS partials"
    )
    return set(rows[0]["partials"]) if rows else set()


async def status() -> None:
    """Print done/pending/total counts. Non-mutating; safe to run anytime."""
    GROUP_ID = DEFAULT_GROUP_ID
    notes = discover_notes()
    all_slugs = {s for _, s, _ in notes}
    g = build_graphiti_on_group(GROUP_ID)
    try:
        done = await _get_done_slugs(g.driver)
        partials = await _get_partial_slugs(g.driver)
    finally:
        await g.close()
    pending = all_slugs - done
    print(f"[status] total notes on disk: {len(all_slugs)}")
    print(f"[status] fully ingested:      {len(done & all_slugs)}")
    print(f"[status] partial (crashed):   {len(partials & all_slugs)}")
    print(f"[status] pending:             {len(pending)}")
    if partials & all_slugs:
        print(f"[status] partial slugs (next cron will clean+re-ingest): "
              f"{sorted(partials & all_slugs)[:5]}")
    pct = (len(done & all_slugs) / len(all_slugs) * 100) if all_slugs else 0
    print(f"[status] progress: {pct:.1f}%")


async def cron() -> None:
    """Resumable bounded batch for the k3s CronJob.

    Idempotent + crash-safe. A note is 'done' iff its episode node exists AND
    has >=1 entity edge; everything else is pending. Partial episodes (crashed
    mid-add_episode) are DETACH-DELETEd first so they re-ingest cleanly.

    Circuit breaker: >= CIRCUIT_BREAKER_THRESHOLD consecutive failures (incl.
    RateLimitError from a blown provider quota window) aborts the batch early so
    we stop burning retries against a down/quota'd upstream; the next cron tick
    retries the same notes. Progress is never lost.
    """
    GROUP_ID = DEFAULT_GROUP_ID
    BATCH = int(os.environ.get("BATCH_SIZE", "15"))
    CIRCUIT_BREAKER_THRESHOLD = int(os.environ.get("CIRCUIT_BREAKER_THRESHOLD", "3"))
    notes_by_slug = {s: (ts, p) for ts, s, p in discover_notes()}
    all_slugs = set(notes_by_slug)
    if not all_slugs:
        print("[cron] no notes found; check NOTES_ROOT", file=sys.stderr)
        return

    g = build_graphiti_on_group(GROUP_ID)
    try:
        # 1. Build indices (idempotent; safe even if already present).
        await g.build_indices_and_constraints()
        # 2. Clean partial episodes (previous crash victims) before re-ingest.
        partials = await _get_partial_slugs(g.driver)
        if partials:
            for slug in partials:
                await g.driver.execute_query(
                    "MATCH (e:Episodic {name: $name}) DETACH DELETE e", name=slug
                )
            print(f"[cron] cleaned {len(partials)} partial episodes: "
                  f"{sorted(partials)[:5]}")
        # 3. Compute pending (sorted oldest-first for stable backfill order).
        done = await _get_done_slugs(g.driver)
        pending = sorted(all_slugs - done)
        print(f"[cron] done={len(done & all_slugs)}/{len(all_slugs)} "
              f"pending={len(pending)}")
        if not pending:
            print("[cron] nothing pending; backfill complete")
            return
        batch = pending[:BATCH]
        print(f"[cron] ingesting batch of {len(batch)} (oldest first)")

        # 4. Ingest with circuit breaker.
        sem = asyncio.Semaphore(CONCURRENCY)
        consecutive_fail = 0
        ok = 0
        fail = 0
        aborted = False

        async def one(slug: str) -> bool:
            nonlocal consecutive_fail, ok, fail
            ts, path = notes_by_slug[slug]
            body = path.read_text(encoding="utf-8", errors="replace")
            async with sem:
                try:
                    await add_episode_with_retry(
                        g,
                        name=slug,
                        episode_body=body,
                        source=EpisodeType.text,
                        source_description="wiki source note",
                        reference_time=ts,
                        group_id=GROUP_ID,
                    )
                    consecutive_fail = 0
                    ok += 1
                    print(f"[cron] ok ({ok}/{len(batch)}) {slug}")
                    return True
                except Exception as e:
                    consecutive_fail += 1
                    fail += 1
                    print(f"[cron] FAIL {slug}: {e!r}", file=sys.stderr)
                    return False

        for slug in batch:
            await one(slug)
            if consecutive_fail >= CIRCUIT_BREAKER_THRESHOLD:
                print(f"[cron] circuit breaker tripped after {consecutive_fail} "
                      f"consecutive failures — aborting batch (next tick retries). "
                      f"Likely LLM quota/upstream issue.")
                aborted = True
                break
        print(f"[cron] batch result: ok={ok} fail={fail} "
              f"{'(aborted by circuit breaker)' if aborted else ''}")
    finally:
        await g.close()


async def query() -> None:
    GROUP_ID = DEFAULT_GROUP_ID
    g = build_graphiti_on_group(GROUP_ID)
    try:
        for q in (
            "What services does the operator run?",
            "What decisions were made recently?",
            "What conventions does the operator follow for storing secrets?",
        ):
            print(f"\n=== query: {q}")
            results = await g.search(q, num_results=5)
            if not results:
                print("  (no results)")
            for r in results:
                # r is an Edge fact; print the fact + validity window if present
                fact = getattr(r, "fact", None) or str(r)
                invalid = getattr(r, "invalid_at", None)
                valid = getattr(r, "valid_at", None)
                print(f"  - {fact}" + (f"  [valid {valid} → {invalid}]" if valid else ""))
    finally:
        await g.close()


async def main() -> None:
    mode = os.environ.get("INGEST_MODE", "ingest")
    if mode in ("ingest", "both"):
        await ingest()
    if mode in ("cron",):
        await cron()
    if mode in ("status",):
        await status()
    if mode in ("query", "both"):
        await query()


if __name__ == "__main__":
    asyncio.run(main())
