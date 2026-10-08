"""Neo4j 5.26 vector retrieval for graph reads, never ingestion matching.

Fulltext, BFS, community search, parsers, and RRF remain upstream implementations.
Indexes are managed explicitly by vector_indexes.py, not by request handlers.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from graphiti_core.driver.driver import GraphProvider
from graphiti_core.driver.neo4j.operations.search_ops import Neo4jSearchOperations
from graphiti_core.driver.record_parsers import entity_edge_from_record, entity_node_from_record
from graphiti_core.graph_queries import get_vector_cosine_func_query
from graphiti_core.models.edges.edge_db_queries import get_entity_edge_return_query
from graphiti_core.models.nodes.node_db_queries import get_entity_node_return_query
from graphiti_core.search.search_filters import (
    edge_search_filter_query_constructor, node_search_filter_query_constructor,
)

log = logging.getLogger(__name__)
PROVIDER = GraphProvider.NEO4J


@dataclass(frozen=True)
class VectorIndex:
    name: str
    entity_type: str
    label: str
    property: str


NODE_INDEX = VectorIndex("dipink_entity_name_vector", "NODE", "Entity", "name_embedding")
EDGE_INDEX = VectorIndex("dipink_fact_vector", "RELATIONSHIP", "RELATES_TO", "fact_embedding")
INDEXES = (NODE_INDEX, EDGE_INDEX)


async def index_metadata(executor, spec):
    rows, _, _ = await executor.execute_query(
        "SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties, options, state "
        "WHERE name = $index_name RETURN *", index_name=spec.name, routing_="r",
    )
    return dict(rows[0]) if rows else None


def index_problem(row, spec, dimensions=None, require_online=True):
    """Return a bounded diagnostic, without database contents or exception text."""
    if row is None:
        return "missing"
    if (row.get("type") != "VECTOR" or row.get("entityType") != spec.entity_type
            or row.get("labelsOrTypes") != [spec.label]
            or row.get("properties") != [spec.property]):
        return "schema"
    config = row.get("options", {}).get("indexConfig", {})
    size = config.get("vector.dimensions")
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= 4096:
        return "dimensions"
    if dimensions is not None and size != dimensions:
        return "dimensions"
    if str(config.get("vector.similarity_function", "")).lower() != "cosine":
        return "similarity"
    if require_online and row.get("state") != "ONLINE":
        return "offline"
    return None


class IndexedReadSearch(Neo4jSearchOperations):
    """Bounded ANN candidates, exact cosine rerank, conservative exact fallback.

    Explicit label/date/edge filters and endpoint constraints use exact search.
    Group-only searches overfetch globally, then filter by group. If fewer than
    limit candidates survive, exact search prevents silent group starvation.
    A full result set is still approximate: quality evaluation gates rollout.
    """

    def __init__(self, *, overfetch=20, max_candidates=2000, dimensions=None):
        if not 1 <= overfetch <= 100 or not 1 <= max_candidates <= 10000:
            raise ValueError("Invalid vector candidate bounds")
        if dimensions is not None and not 1 <= dimensions <= 4096:
            raise ValueError("Invalid vector dimensions")
        self.overfetch = overfetch
        self.max_candidates = max_candidates
        self.dimensions = dimensions

    async def _similarity(self, executor, spec, vector, filters, groups, limit,
                          min_score, source=None, target=None):
        if limit <= 0 or groups == []:
            return []
        edge = spec == EDGE_INDEX
        constructor = edge_search_filter_query_constructor if edge else node_search_filter_query_constructor
        clauses, params = constructor(filters, PROVIDER)
        constrained = bool(clauses or source is not None or target is not None)
        alias = "e" if edge else "n"
        if groups is not None:
            clauses.append(f"{alias}.group_id IN $group_ids")
            params["group_ids"] = groups
        if source is not None:
            clauses.append("n.uuid = $source_uuid")
            params["source_uuid"] = source
        if target is not None:
            clauses.append("m.uuid = $target_uuid")
            params["target_uuid"] = target
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        projection = get_entity_edge_return_query(PROVIDER) if edge else get_entity_node_return_query(PROVIDER)
        cosine = get_vector_cosine_func_query(f"{alias}.{spec.property}", "$search_vector", PROVIDER)
        scope = "e, n, m" if edge else "n"
        tail = (where + f" WITH DISTINCT {scope}, {cosine} AS score "
                "WHERE score > $min_score RETURN " + projection
                + " ORDER BY score DESC LIMIT $limit")
        match = "MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity)" if edge else "MATCH (n:Entity)"
        parser = entity_edge_from_record if edge else entity_node_from_record
        params.update(search_vector=vector, min_score=min_score, limit=limit)

        async def exact(reason):
            log.info("vector retrieval kind=%s mode=exact reason=%s", spec.entity_type, reason)
            rows, _, _ = await executor.execute_query(match + tail, routing_="r", **params)
            return [parser(row) for row in rows]

        if constrained or limit > self.max_candidates:
            return await exact("constrained")
        if self.dimensions is not None and len(vector) != self.dimensions:
            return await exact("query_dimensions")
        try:
            problem = index_problem(await index_metadata(executor, spec), spec, len(vector))
        except Exception as error:
            log.warning("vector metadata unavailable error_type=%s", type(error).__name__)
            return await exact("metadata_error")
        if problem:
            return await exact(problem)
        candidates = min(self.max_candidates, max(100, limit * self.overfetch))
        try:
            if edge:
                prefix = ("CALL db.index.vector.queryRelationships($index_name, $candidates, $search_vector) "
                          "YIELD relationship AS e "
                          "WITH e, startNode(e) AS n, endNode(e) AS m "
                          "WHERE n:Entity AND m:Entity WITH e, n, m")
            else:
                prefix = ("CALL db.index.vector.queryNodes($index_name, $candidates, $search_vector) "
                          "YIELD node AS n WITH n")
            rows, _, _ = await executor.execute_query(
                prefix + tail, index_name=spec.name, candidates=candidates, routing_="r", **params,
            )
        except Exception as error:
            # An unavailable ANN branch must not discard otherwise valid hybrid results.
            # Do not log vectors, query text, credentials, or server exception messages.
            log.warning("vector retrieval kind=%s unavailable error_type=%s", spec.entity_type, type(error).__name__)
            return await exact("index_error")
        if len(rows) < limit:
            return await exact("underfilled")
        log.info("vector retrieval kind=%s mode=indexed candidates=%d", spec.entity_type, candidates)
        return [parser(row) for row in rows]

    async def node_similarity_search(self, executor, search_vector, search_filter,
                                     group_ids=None, limit=10, min_score=0.6):
        return await self._similarity(executor, NODE_INDEX, search_vector, search_filter,
                                      group_ids, limit, min_score)

    async def edge_similarity_search(self, executor, search_vector, source_node_uuid,
                                     target_node_uuid, search_filter, group_ids=None,
                                     limit=10, min_score=0.6):
        return await self._similarity(executor, EDGE_INDEX, search_vector, search_filter,
                                      group_ids, limit, min_score, source_node_uuid, target_node_uuid)


def install_read_search(driver):
    """Opt in only on the graph read client; disabled means unchanged upstream code."""
    if os.environ.get("GRAPH_VECTOR_SEARCH", "0").lower() not in {"1", "true", "yes"}:
        return
    raw_dimensions = os.environ.get("GRAPH_VECTOR_DIMENSIONS", "").strip()
    driver._search_ops = IndexedReadSearch(
        overfetch=int(os.environ.get("GRAPH_VECTOR_OVERFETCH", "20")),
        max_candidates=int(os.environ.get("GRAPH_VECTOR_MAX_CANDIDATES", "2000")),
        dimensions=int(raw_dimensions) if raw_dimensions else None,
    )
