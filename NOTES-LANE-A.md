# Lane A hardening handoff

## Summary

Lane A is in progress. Item 4 is complete: Graphiti now receives an explicitly configured bounded-pool Neo4j driver, Graphiti 0.29.2's constructor-scheduled schema task is avoided, group isolation is property-scoped, and a real Neo4j 5.26.2 lifecycle regression test is present and passing.

## Completed items

### Item 4 — Graphiti/Neo4j lifecycle

- Inspected the exact `graphiti-core==0.29.2` wheel before implementation. Its `Neo4jDriver.__init__` schedules `build_indices_and_constraints()` on the running loop and does not expose pool kwargs; its Neo4j `clone()` is inherited as a no-op, so `GROUP_ID` does not create a database.
- Added `DipInkNeo4jDriver`, an explicit `Neo4jDriver` subclass with a configurable pool (default 40) and no untracked constructor task.
- `Graphiti` receives that driver through `graph_driver=`; no post-construction client replacement remains.
- Schema setup is explicitly awaited only in ingest/setup paths. Read-only server/loop clients do not rebuild schema.
- Direct graph reads/mutations in Lane A-owned files are scoped to `GROUP_ID`; graph searches pass `group_ids`.
- Graph server close now clears its singleton after closing, preventing reuse of a closed client.
- Added unit lifecycle/group-scope tests and an opt-in real Neo4j 5.26.2 integration test covering client create/use/close, graph warm/close, alerts, healthcheck, and ingest status with no unhandled task exceptions or leak warnings.

## Important decisions

- `GROUP_ID` remains a Neo4j node/edge property partition. `NEO4J_DATABASE` (default `neo4j`) is the actual database selector.
- Pool defaults are intentionally bounded (`NEO4J_MAX_POOL=40`, `NEO4J_ACQ_TIMEOUT=30`) rather than the previous 500-connection replacement.
- The integration test is skipped in ordinary unit/image builds and runs when `NEO4J_INTEGRATION=1` points at a real Neo4j 5.26.2 instance.

## Tests run

- `pytest tests/test_driver_lifecycle.py tests/test_neo4j_integration.py -q` — 4 passed, 1 skipped.
- Real `neo4j:5.26.2` container + `pytest tests/test_neo4j_integration.py -q -s` — 1 passed; no unhandled-task, `IncompleteCommit`, defunct-connection, or leak warning signatures.

## Dependencies / coordinator TODOs

- None for item 4.

## Failures / blockers

- None currently.
