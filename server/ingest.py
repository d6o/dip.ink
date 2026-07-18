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
import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from chat_fallback import OrderedModelFallback, parse_model_ladder

from graphiti_core import Graphiti
from graphiti_core.driver.neo4j_driver import Neo4jDriver
from graphiti_core.llm_client import LLMConfig
from graphiti_core.llm_client.client import get_extraction_language_instruction
# OpenAIGenericClient is not re-exported from the llm_client package __init__
# (only OpenAIClient is), so import it from its module directly.
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.llm_client.config import DEFAULT_MAX_TOKENS, ModelSize
from graphiti_core.llm_client.errors import EmptyResponseError, RateLimitError
from graphiti_core.nodes import EpisodeType

log = logging.getLogger("dipink-ingest")


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
            # dip.ink is single-group today. Never discover/operate on every
            # group in a shared Neo4j database by accident.
            group_ids = [os.environ.get("GROUP_ID", "main")]

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
    """OpenAIGenericClient with robustness fixes for non-OpenAI models, plus an
    ordered model-fallback ladder:

    1. **Compact example shape instead of the verbose JSON Schema.** graphiti's
       ``json_object`` mode injects ``model_json_schema()`` (with
       ``$defs``/``$ref``/``properties``) into the prompt; some models mimic
       that template verbatim instead of producing an instance. We override
       ``generate_response`` to inject a COMPACT example shape instead
       (resolved from the schema).

    2. A defensive ``{"answer": ...}`` envelope unwrap — the residual wrapper
       some models occasionally add around the requested object.

    3. **Ordered model fallback** (LLM_MODEL_LADDER): when the active model
       rate-limits or the provider errors, the next model in the ladder is
       tried; with sticky=True this process keeps starting from the model that
       last worked, so a blown quota window doesn't re-fail every prompt.

    Note: if your model does extended thinking by default, disable it at the
    proxy/model level (thinking eats max_tokens as reasoning text so the JSON
    never lands).
    """

    def __init__(self, *args, model_ladder=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.model_ladder = OrderedModelFallback(
            model_ladder or LLM_MODEL_LADDER,
            context="graph-extraction",
            logger=log,
            sticky=True,
        )

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
        openai_messages = []
        for message in messages:
            message.content = self._clean_input(message.content)
            if message.role in ("user", "system"):
                openai_messages.append({"role": message.role, "content": message.content})

        async def call(model: str):
            response = await self.client.chat.completions.create(
                model=model,
                messages=openai_messages,
                temperature=self.temperature,
                max_tokens=max_tokens,
                response_format=self._build_response_format(response_model),
            )
            text = response.choices[0].message.content or ""
            if not text:
                raise EmptyResponseError("LLM returned an empty response")
            return json.loads(self._strip_code_fences(text))

        try:
            result = await self.model_ladder.run(call)
        except Exception as error:
            # Preserve graphiti-core's retry behavior after the ladder exhausts.
            if "429" in str(error) or type(error).__name__ == "RateLimitError":
                raise RateLimitError from error
            raise

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
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")
NEO4J_MAX_POOL = max(1, int(os.environ.get("NEO4J_MAX_POOL", "40")))
NEO4J_ACQ_TIMEOUT = max(1, int(os.environ.get("NEO4J_ACQ_TIMEOUT", "30")))

# Extraction LLM: any OpenAI-compatible chat endpoint. Leave LLM_BASE_URL
# unset to use OpenAI directly (OPENAI_API_KEY + LLM_MODEL, e.g. gpt-4.1-mini);
# point it at a LiteLLM/CLIProxy/other proxy to use different models.
# LLM_MODEL_LADDER (comma-separated) enables ordered fallback across models on
# the same endpoint; it defaults to just LLM_MODEL.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "").strip()
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
LLM_MODEL_LADDER = parse_model_ladder(
    os.environ.get("LLM_MODEL_LADDER") or os.environ.get("LLM_MODEL") or "gpt-4.1-mini"
)
LLM_MODEL = LLM_MODEL_LADDER[0]

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


class DipInkNeo4jDriver(Neo4jDriver):
    """Neo4jDriver with an explicit bounded pool and no constructor task.

    graphiti-core 0.29.2's ``Neo4jDriver.__init__`` creates the async driver and
    immediately schedules ``build_indices_and_constraints()`` when called from
    a running loop. Short-lived read-only jobs can then close the driver while
    that untracked task is still using it. The upstream constructor also does
    not expose Neo4j pool kwargs. Keep the upstream operations implementation,
    but construct its client explicitly so callers decide when schema setup is
    awaited (ingest/setup paths only).
    """

    def __init__(
        self,
        uri: str,
        user: str | None,
        password: str | None,
        *,
        database: str = "neo4j",
        max_connection_pool_size: int = 40,
        connection_acquisition_timeout: int = 30,
    ) -> None:
        from neo4j import AsyncGraphDatabase
        from graphiti_core.driver.neo4j.operations.community_edge_ops import Neo4jCommunityEdgeOperations
        from graphiti_core.driver.neo4j.operations.community_node_ops import Neo4jCommunityNodeOperations
        from graphiti_core.driver.neo4j.operations.entity_edge_ops import Neo4jEntityEdgeOperations
        from graphiti_core.driver.neo4j.operations.entity_node_ops import Neo4jEntityNodeOperations
        from graphiti_core.driver.neo4j.operations.episode_node_ops import Neo4jEpisodeNodeOperations
        from graphiti_core.driver.neo4j.operations.episodic_edge_ops import Neo4jEpisodicEdgeOperations
        from graphiti_core.driver.neo4j.operations.graph_ops import Neo4jGraphMaintenanceOperations
        from graphiti_core.driver.neo4j.operations.has_episode_edge_ops import Neo4jHasEpisodeEdgeOperations
        from graphiti_core.driver.neo4j.operations.next_episode_edge_ops import Neo4jNextEpisodeEdgeOperations
        from graphiti_core.driver.neo4j.operations.saga_node_ops import Neo4jSagaNodeOperations
        from graphiti_core.driver.neo4j.operations.search_ops import Neo4jSearchOperations

        self.client = AsyncGraphDatabase.driver(
            uri=uri,
            auth=(user or "", password or ""),
            max_connection_pool_size=max_connection_pool_size,
            connection_acquisition_timeout=connection_acquisition_timeout,
        )
        self._database = database
        self._entity_node_ops = Neo4jEntityNodeOperations()
        self._episode_node_ops = Neo4jEpisodeNodeOperations()
        self._community_node_ops = Neo4jCommunityNodeOperations()
        self._saga_node_ops = Neo4jSagaNodeOperations()
        self._entity_edge_ops = Neo4jEntityEdgeOperations()
        self._episodic_edge_ops = Neo4jEpisodicEdgeOperations()
        self._community_edge_ops = Neo4jCommunityEdgeOperations()
        self._has_episode_edge_ops = Neo4jHasEpisodeEdgeOperations()
        self._next_episode_edge_ops = Neo4jNextEpisodeEdgeOperations()
        self._search_ops = Neo4jSearchOperations()
        self._graph_ops = Neo4jGraphMaintenanceOperations()
        self.aoss_client = None


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
    # OPENAI_API_KEY from env. Pass the fully configured driver up front: never
    # swap Graphiti's client after construction.
    graph_driver = DipInkNeo4jDriver(
        NEO4J_URI,
        NEO4J_USER,
        NEO4J_PASSWORD,
        database=NEO4J_DATABASE,
        max_connection_pool_size=NEO4J_MAX_POOL,
        connection_acquisition_timeout=NEO4J_ACQ_TIMEOUT,
    )
    return Graphiti(
        graph_driver=graph_driver,
        llm_client=llm_client,
        embedder=None,
    )


DEFAULT_GROUP_ID = os.environ.get("GROUP_ID", "main")


def build_graphiti_on_group(group_id: str = DEFAULT_GROUP_ID) -> Graphiti:
    """Build a client for callers that scope graph data by ``group_id``.

    ``group_id`` is a property-level partition in Neo4j, not a database name.
    The argument is retained for call-site clarity and backward compatibility;
    every query still has to pass/filter it explicitly.
    """
    if not group_id:
        raise ValueError("group_id must be non-empty")
    return build_graphiti()


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
            # Quarantined notes are intentionally not ingest candidates.
            if ".blocked" in md.relative_to(root).parts:
                continue
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


_NOTE_HASH_CACHE: dict[Path, tuple[int, int, str]] = {}


def episode_content_hash(content: str) -> str:
    """Hash the exact UTF-8 text passed to Graphiti as ``episode_body``."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def read_note_body(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def note_content_hash(path: Path) -> str:
    """Content hash with a cheap stat-keyed cache for repeated status scrapes."""
    stat = path.stat()
    cached = _NOTE_HASH_CACHE.get(path)
    key = (stat.st_mtime_ns, stat.st_size)
    if cached is not None and cached[:2] == key:
        return cached[2]
    digest = episode_content_hash(read_note_body(path))
    _NOTE_HASH_CACHE[path] = (key[0], key[1], digest)
    return digest


@dataclass(frozen=True)
class EpisodeState:
    slug: str
    uuid: str
    complete: bool | None
    content_hash: str | None
    completed_at: str | None
    created_at: str | None
    legacy_content: str | None
    mention_count: int


@dataclass
class IngestAssessment:
    notes_by_slug: dict[str, tuple[datetime, Path]]
    note_hashes: dict[str, str]
    episodes_by_slug: dict[str, list[EpisodeState]]
    done: set[str]
    partial: set[str]
    changed: set[str]
    missing: set[str]
    legacy_compatible: set[str]
    legacy_upgraded: int = 0

    @property
    def pending(self) -> set[str]:
        return self.partial | self.changed | self.missing

    def as_dict(self) -> dict:
        pending_notes = [
            (self.notes_by_slug[slug][0], slug)
            for slug in self.pending
            if slug in self.notes_by_slug
        ]
        pending_notes.sort()
        now = datetime.now(timezone.utc)
        oldest_pending = pending_notes[0] if pending_notes else None
        newest_note = max(
            ((ts, slug) for slug, (ts, _path) in self.notes_by_slug.items()),
            default=None,
        )
        all_episodes = [ep for episodes in self.episodes_by_slug.values() for ep in episodes]
        newest_episode = max(
            all_episodes,
            key=lambda ep: ep.completed_at or ep.created_at or "",
            default=None,
        )
        completed_values = [ep.completed_at for ep in all_episodes if ep.completed_at]
        return {
            "total": len(self.notes_by_slug),
            "done": len(self.done),
            "pending": len(self.pending),
            "partial": len(self.partial),
            "changed": len(self.changed),
            "missing": len(self.missing),
            "legacy_compatible": len(self.legacy_compatible),
            "legacy_upgraded": self.legacy_upgraded,
            "pending_slugs": [slug for _ts, slug in pending_notes[:20]],
            "partial_slugs": sorted(self.partial)[:20],
            "changed_slugs": sorted(self.changed)[:20],
            "oldest_pending_at": oldest_pending[0].isoformat() if oldest_pending else None,
            "lag_seconds": max(0.0, (now - oldest_pending[0]).total_seconds())
            if oldest_pending else 0.0,
            "newest_note": {
                "slug": newest_note[1],
                "at": newest_note[0].isoformat(),
            } if newest_note else None,
            "newest_ingested_episode": {
                "slug": newest_episode.slug,
                "completed_at": newest_episode.completed_at,
                "created_at": newest_episode.created_at,
            } if newest_episode else None,
            "ingest_watermark": max(completed_values) if completed_values else None,
        }


async def _get_episode_states(
    driver, group_id: str = DEFAULT_GROUP_ID
) -> dict[str, list[EpisodeState]]:
    """Read explicit ingest metadata plus just enough legacy state to classify."""
    rows, _, _ = await driver.execute_query(
        "MATCH (e:Episodic {group_id: $group_id}) "
        "OPTIONAL MATCH (e)-[m:MENTIONS]->(:Entity {group_id: $group_id}) "
        "WITH e, count(m) AS mention_count "
        "RETURN e.name AS slug, e.uuid AS uuid, "
        "e.dipink_ingest_complete AS complete, "
        "e.dipink_content_hash AS content_hash, "
        "toString(e.dipink_completed_at) AS completed_at, "
        "toString(e.created_at) AS created_at, "
        "CASE WHEN e.dipink_content_hash IS NULL THEN e.content ELSE null END AS legacy_content, "
        "mention_count ORDER BY e.created_at DESC",
        group_id=group_id,
        routing_="r",
    )
    out: dict[str, list[EpisodeState]] = {}
    for row in rows:
        slug = str(row.get("slug") or "")
        uuid = str(row.get("uuid") or "")
        if not slug or not uuid:
            continue
        out.setdefault(slug, []).append(EpisodeState(
            slug=slug,
            uuid=uuid,
            complete=row.get("complete"),
            content_hash=str(row["content_hash"]) if row.get("content_hash") else None,
            completed_at=str(row["completed_at"]) if row.get("completed_at") else None,
            created_at=str(row["created_at"]) if row.get("created_at") else None,
            legacy_content=row.get("legacy_content"),
            mention_count=int(row.get("mention_count") or 0),
        ))
    return out


async def _mark_episode_complete(
    driver,
    episode_uuid: str,
    content_hash: str,
    group_id: str = DEFAULT_GROUP_ID,
) -> None:
    rows, _, _ = await driver.execute_query(
        "MATCH (e:Episodic {uuid: $uuid, group_id: $group_id}) "
        "SET e.dipink_ingest_complete = true, "
        "e.dipink_content_hash = $content_hash, "
        "e.dipink_completed_at = coalesce(e.dipink_completed_at, datetime()) "
        "RETURN e.uuid AS uuid",
        uuid=episode_uuid,
        group_id=group_id,
        content_hash=content_hash,
    )
    if not rows:
        raise RuntimeError(f"episode disappeared before completion metadata: {episode_uuid}")


def _classify_note(
    expected_hash: str,
    episodes: list[EpisodeState],
) -> tuple[str, EpisodeState | None]:
    """Return (done|partial|changed|missing, safe legacy upgrade candidate)."""
    if not episodes:
        return "missing", None

    # Explicit completion is authoritative, including valid zero-fact episodes.
    for episode in episodes:
        if episode.complete is not True:
            continue
        if episode.content_hash == expected_hash:
            return "done", None
        if episode.content_hash is None and episode.legacy_content is not None:
            if episode_content_hash(episode.legacy_content) == expected_hash:
                return "done", episode
            return "changed", None
        if episode.content_hash is None:
            # Completed by a pre-hash writer, but content is unavailable. Keep
            # compatibility without inventing a hash we cannot verify safely.
            return "done", None
        return "changed", None

    # Legacy edge-based compatibility: an episode with extracted facts counted
    # as complete before dip.ink added explicit metadata. Upgrade only when its
    # stored body proves it is the same source content.
    for episode in episodes:
        if episode.mention_count <= 0:
            continue
        if episode.legacy_content is None:
            return "done", None
        if episode_content_hash(episode.legacy_content) == expected_hash:
            return "done", episode
        return "changed", None

    # No completion marker and no entity mentions is a crash-created partial.
    return "partial", None


async def assess_ingest(
    driver,
    notes: list[tuple[datetime, str, Path]] | None = None,
    *,
    group_id: str = DEFAULT_GROUP_ID,
    upgrade_legacy: bool = True,
) -> IngestAssessment:
    notes = discover_notes() if notes is None else notes
    notes_by_slug = {slug: (ts, path) for ts, slug, path in notes}
    note_hashes = {slug: note_content_hash(path) for slug, (_ts, path) in notes_by_slug.items()}
    episodes_by_slug = await _get_episode_states(driver, group_id)
    buckets = {name: set() for name in ("done", "partial", "changed", "missing")}
    legacy_compatible: set[str] = set()
    upgrades: list[tuple[EpisodeState, str]] = []

    for slug, expected_hash in note_hashes.items():
        disposition, upgrade = _classify_note(expected_hash, episodes_by_slug.get(slug, []))
        buckets[disposition].add(slug)
        if upgrade is not None:
            legacy_compatible.add(slug)
            upgrades.append((upgrade, expected_hash))

    upgraded = 0
    if upgrade_legacy:
        for episode, expected_hash in upgrades:
            await _mark_episode_complete(driver, episode.uuid, expected_hash, group_id)
            upgraded += 1
        if upgraded:
            # Return the fresh completion timestamp/hash immediately so status
            # watermarks and answer-cache invalidation see the lazy migration.
            episodes_by_slug = await _get_episode_states(driver, group_id)

    return IngestAssessment(
        notes_by_slug=notes_by_slug,
        note_hashes=note_hashes,
        episodes_by_slug=episodes_by_slug,
        done=buckets["done"],
        partial=buckets["partial"],
        changed=buckets["changed"],
        missing=buckets["missing"],
        legacy_compatible=legacy_compatible,
        legacy_upgraded=upgraded,
    )


async def collect_ingest_status(
    g=None,
    notes: list[tuple[datetime, str, Path]] | None = None,
    *,
    group_id: str = DEFAULT_GROUP_ID,
    upgrade_legacy: bool = True,
) -> dict:
    """Structured ingest state shared by CLI status, API status, and metrics."""
    own_client = g is None
    client = build_graphiti_on_group(group_id) if own_client else g
    try:
        assessment = await assess_ingest(
            client.driver,
            notes,
            group_id=group_id,
            upgrade_legacy=upgrade_legacy,
        )
        return assessment.as_dict()
    finally:
        if own_client:
            await client.close()


async def _get_done_slugs(driver, group_id: str = DEFAULT_GROUP_ID) -> set[str]:
    """Compatibility helper: explicit completion or legacy entity mentions."""
    states = await _get_episode_states(driver, group_id)
    return {
        slug for slug, episodes in states.items()
        if any(ep.complete is True or ep.mention_count > 0 for ep in episodes)
    }


async def _get_partial_slugs(driver, group_id: str = DEFAULT_GROUP_ID) -> set[str]:
    """Compatibility helper for crash-created, not-explicitly-complete episodes."""
    states = await _get_episode_states(driver, group_id)
    return {
        slug for slug, episodes in states.items()
        if episodes and not any(ep.complete is True or ep.mention_count > 0 for ep in episodes)
    }


async def _remove_episode_for_retry(
    g,
    episode: EpisodeState,
    *,
    group_id: str,
    reason: str,
) -> None:
    """Use Graphiti's removal path, with scoped cleanup for malformed partials."""
    try:
        await g.remove_episode(episode.uuid)
        return
    except Exception as error:  # noqa: BLE001
        log.warning(
            "Graphiti remove_episode failed for %s (%s); scoped fallback: %s",
            episode.slug,
            reason,
            type(error).__name__,
        )
    await g.driver.execute_query(
        "MATCH (e:Episodic {uuid: $uuid, group_id: $group_id}) DETACH DELETE e",
        uuid=episode.uuid,
        group_id=group_id,
    )


async def _prepare_note_for_reingest(
    g,
    assessment: IngestAssessment,
    slug: str,
    *,
    group_id: str,
) -> None:
    if slug not in assessment.partial and slug not in assessment.changed:
        return
    reason = "changed-content" if slug in assessment.changed else "partial"
    for episode in assessment.episodes_by_slug.get(slug, []):
        await _remove_episode_for_retry(g, episode, group_id=group_id, reason=reason)


async def _ingest_note(
    g,
    ts: datetime,
    slug: str,
    path: Path,
    *,
    group_id: str,
) -> None:
    body = read_note_body(path)
    result = await add_episode_with_retry(
        g,
        name=slug,
        episode_body=body,
        source=EpisodeType.text,
        source_description="wiki source note",
        reference_time=ts,
        group_id=group_id,
    )
    episode = getattr(result, "episode", None)
    episode_uuid = str(getattr(episode, "uuid", "") or "")
    if not episode_uuid:
        raise RuntimeError(f"Graphiti add_episode returned no episode uuid for {slug}")
    await _mark_episode_complete(g.driver, episode_uuid, episode_content_hash(body), group_id)


async def _run_pending_batch(
    g,
    assessment: IngestAssessment,
    selected: list[tuple[datetime, str, Path]],
    *,
    group_id: str,
    prefix: str,
    circuit_breaker_threshold: int | None = None,
) -> tuple[int, int, bool]:
    ok = 0
    fail = 0
    consecutive_fail = 0
    aborted = False
    for ts, slug, path in selected:
        try:
            await _prepare_note_for_reingest(g, assessment, slug, group_id=group_id)
            await _ingest_note(g, ts, slug, path, group_id=group_id)
            consecutive_fail = 0
            ok += 1
            print(f"[{prefix}] ok ({ok}/{len(selected)}) {slug}")
        except Exception as error:  # one note failing must not kill the run
            consecutive_fail += 1
            fail += 1
            print(f"[{prefix}] FAIL {slug}: {error!r}", file=sys.stderr)
        if circuit_breaker_threshold and consecutive_fail >= circuit_breaker_threshold:
            print(
                f"[{prefix}] circuit breaker tripped after {consecutive_fail} consecutive "
                "failures — aborting batch (next tick retries)."
            )
            aborted = True
            break
    return ok, fail, aborted


async def ingest() -> None:
    notes = discover_notes()
    total = len(notes)
    print(f"[ingest] discovered {total} canonical source notes under {NOTES_ROOT}")
    if total == 0:
        print("[ingest] nothing to ingest; check NOTES_ROOT / git clone", file=sys.stderr)
        return

    group_id = DEFAULT_GROUP_ID
    g = build_graphiti_on_group(group_id)
    try:
        print(f"[ingest] building indices and constraints on Neo4j database '{NEO4J_DATABASE}'...")
        await g.build_indices_and_constraints()
        assessment = await assess_ingest(g.driver, notes, group_id=group_id)
        selected = [note for note in notes if note[1] in assessment.pending][:NOTE_LIMIT]
        print(
            f"[ingest] done={len(assessment.done)}/{total} pending={len(assessment.pending)} "
            f"partial={len(assessment.partial)} changed={len(assessment.changed)}"
        )
        if not selected:
            print("[ingest] nothing pending")
            return
        ok, fail, _aborted = await _run_pending_batch(
            g, assessment, selected, group_id=group_id, prefix="ingest"
        )
        print(f"[ingest] done: {ok}/{len(selected)} ok, {fail} failed")
    finally:
        await g.close()


async def status() -> None:
    """Print explicit completion/content-hash ingest state.

    Safe legacy episodes with entity edges are lazily upgraded with completion
    metadata while status is computed.
    """
    snapshot = await collect_ingest_status()
    print(f"[status] total notes on disk: {snapshot['total']}")
    print(f"[status] fully ingested:      {snapshot['done']}")
    print(f"[status] partial (crashed):   {snapshot['partial']}")
    print(f"[status] changed content:     {snapshot['changed']}")
    print(f"[status] pending:             {snapshot['pending']}")
    if snapshot["partial_slugs"]:
        print(f"[status] partial slugs: {snapshot['partial_slugs'][:5]}")
    if snapshot["changed_slugs"]:
        print(f"[status] changed slugs: {snapshot['changed_slugs'][:5]}")
    pct = (snapshot["done"] / snapshot["total"] * 100) if snapshot["total"] else 0
    print(f"[status] progress: {pct:.1f}%")


async def cron() -> None:
    """Resumable bounded batch with explicit completion and content identity.

    Zero-fact episodes are complete when their marker/hash is present. Legacy
    edge-bearing episodes remain compatible and are lazily upgraded. Partials
    are removed and retried; changed source content is deliberately removed via
    Graphiti's episode-removal path and re-added once.
    """
    group_id = DEFAULT_GROUP_ID
    batch_size = int(os.environ.get("BATCH_SIZE", "15"))
    breaker = int(os.environ.get("CIRCUIT_BREAKER_THRESHOLD", "3"))
    notes = discover_notes()
    if not notes:
        print("[cron] no notes found; check NOTES_ROOT", file=sys.stderr)
        return

    g = build_graphiti_on_group(group_id)
    try:
        await g.build_indices_and_constraints()
        assessment = await assess_ingest(g.driver, notes, group_id=group_id)
        pending = [note for note in notes if note[1] in assessment.pending]
        print(
            f"[cron] done={len(assessment.done)}/{len(notes)} pending={len(pending)} "
            f"partial={len(assessment.partial)} changed={len(assessment.changed)} "
            f"legacy_upgraded={assessment.legacy_upgraded}"
        )
        if not pending:
            print("[cron] nothing pending; backfill complete")
            return
        selected = pending[:batch_size]
        print(f"[cron] ingesting batch of {len(selected)} (oldest first)")
        ok, fail, aborted = await _run_pending_batch(
            g,
            assessment,
            selected,
            group_id=group_id,
            prefix="cron",
            circuit_breaker_threshold=breaker,
        )
        print(
            f"[cron] batch result: ok={ok} fail={fail} "
            f"{'(aborted by circuit breaker)' if aborted else ''}"
        )
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
            results = await g.search(q, group_ids=[GROUP_ID], num_results=5)
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
