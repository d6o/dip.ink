"""Read-only metadata-first inspection of episodes with no recorded edge IDs.

Zero edges can be a valid completed extraction. Missing edge metadata is not
proof of zero actual edges. --verify-links counts provenance links, never facts.
"""
import argparse
import json
import logging
import os
from neo4j import GraphDatabase


def inspect(driver, *, group, database, limit=20, verify_links=False):
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    rows, _, _ = driver.execute_query(
        "MATCH (e:Episodic {group_id: $group}) "
        "WHERE e.entity_edges IS NULL OR size(e.entity_edges) = 0 "
        "RETURN e.uuid AS uuid, e.name AS slug, e.dipink_ingest_complete AS complete, "
        "e.entity_edges IS NOT NULL AS edge_metadata_present, "
        "toString(e.created_at) AS created_at, toString(e.dipink_completed_at) AS completed_at "
        "ORDER BY e.uuid LIMIT $limit",
        group=group, limit=limit, database_=database, routing_="r",
    )
    output = []
    for record in rows:
        row = dict(record)
        row["classification"] = ("completed_recorded_zero_not_proven_fault" if row["complete"] is True and row["edge_metadata_present"]
                                 else "edge_metadata_missing" if not row["edge_metadata_present"] else "completion_unconfirmed")
        if verify_links:
            counts, _, _ = driver.execute_query(
                "MATCH ()-[r:RELATES_TO]->() WHERE r.group_id = $group AND $uuid IN coalesce(r.episodes, []) "
                "RETURN count(r) AS linked_edges",
                group=group, uuid=row["uuid"], database_=database, routing_="r",
            )
            row["linked_edges"] = counts[0]["linked_edges"]
            row["classification"] = "provenance_edges_present" if row["linked_edges"] else "no_linked_edges_not_proven_fault"
        output.append(row)
    return {"group_id": group, "database": database, "sample_limit": limit, "returned": len(output),
            "interpretation": "bounded metadata sample; not an extraction-quality verdict", "episodes": output}


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--verify-links", action="store_true")
    args = parser.parse_args()
    try:
        with GraphDatabase.driver(os.environ.get("NEO4J_URI", "bolt://neo4j:7687"),
                                  auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
                                  max_connection_pool_size=1) as driver:
            print(json.dumps(inspect(driver, group=os.environ.get("GROUP_ID", "main"),
                                     database=os.environ.get("NEO4J_DATABASE", "neo4j"),
                                     limit=args.limit, verify_links=args.verify_links), sort_keys=True))
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__}))
        raise SystemExit(1)
