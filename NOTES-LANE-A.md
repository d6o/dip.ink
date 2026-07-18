# Lane A hardening handoff

## Summary

Lane A is in progress. Items 4 and 9 are complete: Neo4j lifecycle is explicit and bounded, and ingest now has durable completion/content identity that correctly handles zero-fact, partial, changed, unchanged, and legacy episodes.

## Completed items

### Item 4 — Graphiti/Neo4j lifecycle

- Inspected the exact `graphiti-core==0.29.2` wheel before implementation. Its `Neo4jDriver.__init__` schedules `build_indices_and_constraints()` on the running loop and does not expose pool kwargs; its Neo4j `clone()` is inherited as a no-op, so `GROUP_ID` does not create a database.
- Added `DipInkNeo4jDriver`, an explicit `Neo4jDriver` subclass with a configurable pool (default 40) and no untracked constructor task.
- `Graphiti` receives that driver through `graph_driver=`; no post-construction client replacement remains.
- Schema setup is explicitly awaited only in ingest/setup paths. Read-only server/loop clients do not rebuild schema.
- Direct graph reads/mutations in Lane A-owned files are scoped to `GROUP_ID`; graph searches pass `group_ids`.
- Graph server close now clears its singleton after closing, preventing reuse of a closed client.
- Added unit lifecycle/group-scope tests and an opt-in real Neo4j 5.26.2 integration test covering client create/use/close, graph warm/close, alerts, healthcheck, and ingest status with no unhandled task exceptions or leak warnings.

### Item 9 — explicit ingest completion + content hash

- Successful `add_episode` calls now mark the scoped Episodic node with `dipink_ingest_complete`, an exact UTF-8 episode-body SHA-256 in `dipink_content_hash`, and `dipink_completed_at`.
- Explicit completion is authoritative even when extraction produces zero facts.
- Legacy edge-bearing episodes still count as done; when stored content matches the source note they are lazily upgraded with the new metadata.
- Crash-created zero-edge/unmarked episodes are classified as partial, removed, and retried.
- Changed source content is detected by hash, removed through Graphiti's `remove_episode` path (with a scoped malformed-partial fallback), and deliberately re-added once.
- Ingest/status/cron share one structured assessment, process only pending notes oldest-first, expose pending/partial/changed/lag/watermark state, and exclude `notes/.blocked/` from discovery.
- The deep healthcheck now accepts explicit zero-fact completion and legacy edge compatibility, but not an unmarked zero-edge partial.
- Added focused tests for zero-fact completion, partial cleanup/retry, changed remove/re-add, unchanged idempotency, legacy migration, and blocked discovery.

## Important decisions

- `GROUP_ID` remains a Neo4j node/edge property partition. `NEO4J_DATABASE` (default `neo4j`) is the actual database selector.
- Pool defaults are intentionally bounded (`NEO4J_MAX_POOL=40`, `NEO4J_ACQ_TIMEOUT=30`) rather than the previous 500-connection replacement.
- The integration test is skipped in ordinary unit/image builds and runs when `NEO4J_INTEGRATION=1` points at a real Neo4j 5.26.2 instance.
- The ingest hash covers the exact decoded text passed as Graphiti's `episode_body`, not file metadata or extracted-fact count.
- Legacy completion is upgraded only when the stored episode body proves the hash safely; compatibility remains read-only when historical content is unavailable.

## Tests run

- `pytest tests/test_driver_lifecycle.py tests/test_neo4j_integration.py -q` — 4 passed, 1 skipped.
- Real `neo4j:5.26.2` container + `pytest tests/test_neo4j_integration.py -q -s` — 1 passed; no unhandled-task, `IncompleteCommit`, defunct-connection, or leak warning signatures; legacy completion metadata verified in Neo4j.
- `pytest tests/test_ingest_completion.py tests/test_driver_lifecycle.py -q -s` — 10 passed.
- Full server suite after item 9 — 19 passed, 1 skipped.

## Dependencies / coordinator TODOs

- None for item 4.

## Failures / blockers

- None currently.
