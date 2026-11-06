# Latest-supported-state policy candidate (v0.1.61)

This candidate addresses evidence loss and false authority, not general semantic verification. Production remains hybrid. No source, fact, ingestion matching rule, provider, transport setting, or output schema is replaced.

## Evidence eligibility

Readable evidence no longer expires because another source has a newer capture timestamp. The global 24-hour cutoff and unreadable-newer-label veto are removed. Current/non-inactive facts and readable source excerpts remain available regardless of age. Unattributed entity summaries and names-only hits still cannot support an answer.

Capture dates remain provenance metadata. `window_seconds` is now null in the internal temporal context. `eligible_sources` means readable candidates, not proof of activation, scope, or authority. Undated readable ordinary state evidence can participate without an invented `as_of` timestamp. Genuine deployment identifiers and named-date questions retain their dated-support checks.

Common present-tense configuration forms now enter the state path without the word `current`. Explicit original, past, and historical requests retain their ordinary/history path. This is a small extension to the existing intent selector, not a new grammar framework.

## One existing reader, explicit state/conflict policy

The existing distiller must reason from completed effective changes, confirmed observations, corrections, and rollbacks in the requested project/setting/environment. It must not activate a proposal, experiment, future plan, or published artifact as a deployment. Publication can establish publication status when that is what the question asks.

Stable supported state remains usable until a supported change replaces it. Capture order, note count, list position, and highest numeric version are not state authority. The numeric higher-patch veto is removed: a newer publication or another setting must not invalidate a valid deployed-state citation.

Equal unresolved mutually exclusive reports require `not_found`, a null answer, and escalation. The reader needs actual effective chronology or resolution evidence before choosing a winner. Code does not implement a last-writer, latest-capture, highest-version, or arbitrary tie-break.

There is no extra model call, judge, provider-specific exception, or deterministic prose reducer. Completion/scope/conflict interpretation remains a responsibility of the existing reader. Literal citation and identifier validation cannot prove that interpretation. A model that ignores the policy can still make a wrong scoped-state claim; provider trials are a release gate, not optional proof replaced by unit tests.

## Preserved validation and output

- Citation identities, readable support, named subjects, dates, projects, and the two older qualifier modules remain.
- Invented versions/commits and unsupported actual deployment UUIDs/artifact references still fail.
- A short hex commit can match a full hex hash, not a similarly prefixed non-hash name.
- Present-state answers remain qualified as recorded evidence, not a live check, with escalation and at most medium confidence.
- `as_of`, when present, is the latest cited source timestamp. It is not the effective activation time. Undated ordinary evidence gets no invented date.

## Request-time validity flags

Known validity intervals now use `[valid_at, invalid_at)` at one request time:

- A future start is not active yet.
- A future end does not prematurely deactivate a fact.
- A fact is inactive at its exact end boundary.
- Naive ISO timestamps are interpreted as UTC.
- Malformed nonempty bounds fail closed.
- Missing bounds remain open for legacy compatibility. This flag does **not** prove an unknown start or actual active state; source interpretation still matters.

`graph_changes` applies the same request time, excludes future starts and future supersessions, and excludes inverted/empty known intervals. A missing start cannot qualify as a newly activated fact in a date window. Existing response fields remain unchanged.

This is not a valid-as-of plus known-as-of API, and it does not reconstruct transaction history. The older synthetic bitemporal specification remains a gap inventory; its v0.1.60 diagnostic text is historical. No production graph metadata was rewritten.

## Intentional regression expectation changes

Tests that expected age alone, a newer unreadable label, or a higher patch number to reject a citation now expect retained readable evidence. This includes old role/hold support beside a newer unrelated record. Tests that used numeric ordering to catch obsolete quoted values now verify that the warning context reaches the reader; they no longer mislabel that heuristic as semantic verification.

Direct invented-source/value, named-date/project, operational-role, hold, snapshot, and credential-method negatives remain. New tests exercise publication versus deployment, unrelated settings/environments, current identifier support, unknown timestamps, and future interval boundaries.

## Verification boundary and rollout gate

All 14 present-state fixtures are checked for supporting-source visibility through production preparation. The conflict control retains both reports in either order and verifies the explicit uncertainty instruction with a mocked response. These tests do **not** establish reader accuracy.

The isolated Neo4j interval test seeds timing metadata only to check query mechanics. It is not an end-to-end source-ingestion freshness trial. Session A must run the approved reader controls with the same captured evidence, then actual isolated ingestion/retrieval trials, before publication or deployment. Missing retrieval/hydration context can still defeat the reader; budgets and retrieval ranking are unchanged.

Rollback is the prior immutable image. This candidate changes no stored facts, source files, or schema, so rollback needs no data rewrite. Janitor suspension, ingestion structured-output settings, and notification scope remain unchanged.
