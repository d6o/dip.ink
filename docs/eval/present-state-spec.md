# Primary benchmark: latest supported present state

The product goal is ordinary configuration questions that return the latest supported state, even when many older notes repeat the prior value. Historical point-in-time querying is secondary. This contract does not authorize a production change or removal of Graphiti.

The compact [generator](../../server/evaluation_cases/present_state_v1.py) produces 14 synthetic scenarios. It does not implement a state reducer, an answer classifier, or a backend trial. All architecture/reader trial statuses remain **unrun**.

## Scenarios

1. One completed switch against 100 repeated old records.
2. The same evidence and identities, with arrival/reader order shuffled.
3. An older event captured after the completed switch.
4. A proposal that does not activate a change, also against 100 old records.
5. An isolated experiment that does not change production.
6. A completed change for another project.
7. A completed change for another setting in the same project.
8. A completed change for another environment.
9. A stable old fact with no subsequent change receipt.
10. A completed rollback after repeated intermediate-state records.
11. A future plan without activation.
12. A correction that retracts an earlier false rollout report.
13. Simultaneous unresolved conflicting reports, requiring uncertainty.
14. One completed switch followed by 100 late captures of older history.

The questions use ordinary present-tense wording and explicit scope. They contain neither a required `current` keyword nor a historical query date. The assessment clock is fixed in case metadata for reproducibility; it is not an event/effective date supplied as an oracle.

## Inputs versus grader metadata

Each source contains exactly `id`, `captured_at`, and raw `text`. Identities are neutral and timestamps represent capture, not authority. Event/effective dates, completion, plans, correction, and scope appear only in prose. No source contains an expected value, event type, current flag, validity interval, or precomputed state.

`grader` contains manually reviewed expected values, supporting source identities, forbidden active values, and uncertainty expectations. `controls`, scenario labels, and trial statuses are also evaluation metadata. They must not enter retrieval or reader input.

```python
from evaluation_cases.present_state_v1 import build, source_inputs, reader_packet

benchmark = build()
case = benchmark["cases"][0]
inputs = source_inputs(case)       # ingestion input; excludes grader/controls
packet = reader_packet(case)       # existing frozen reader-packet shape
```

The packet adapter includes every raw source body under its original identity. It supplies no graph facts or hand-labeled current flags. All generated cases fit the existing 20,000-character body budget, including the 100-record controls. Oversized controls fail instead of silently dropping evidence. Repetition is generated from templates; no large duplicated JSON is committed.

The source-order control preserves exact source identities, prose, and timestamps. Only input order changes. Multiple cases defeat a newest-capture shortcut, and equal-size cases require different answers. The expectation labels are a test contract, not predictions from an implemented reducer.

## Separate the trials

**All-evidence reader control:** give a fixed reader every source, the ordinary question, and the fixed assessment clock. Record the actual model request and visible source identities. Do not retrieve, rank, or trim this control. This isolates interpretation of chronology/completion/scope from retrieval and ingestion. Expected values and gold sources remain grader-only.

**Prepared-reader control:** the existing `run_readers` helper can consume the frozen packet shape with caller-supplied corpus/vector metadata. Its shared subject/temporal preparation can remove sources. Inspect its captured model packet. If it prunes evidence, label the trial prepared-reader, **not all-evidence**. Do not change production guards or silently inject eligibility flags to force a pass.

**End-to-end backend trial:** use a disposable backend separate from production, with an isolated namespace or reset fixture per case. Never load these sources into the private wiki or production graph. Ingest every actual raw source through that architecture's normal extraction/index/update path, in the declared order. Then ask the unchanged question through normal retrieval and answer generation. Graphiti and a simple state projection must derive their state from prose; never populate expected current values, invalidation flags, or supersession links by hand.

For arrival-order trials, deliver one source at a time in the listed order and complete its normal ingest step before delivering the next. Keep capture timestamps unchanged. Bulk preloading followed by timestamp sorting does not test out-of-order arrival; record the actual processed order.

Keep independent scenario worlds separate; several intentionally reuse the same setting with different histories. The shuffled control must run in a fresh isolated namespace with the same source identities, not append duplicates to the baseline run. Freeze and record extraction, embedding, retrieval, reader, source order, and assessment-clock settings. Preserve ingestion failures and missing answer receipts in the denominator.

The old 30-case lookup/reader dataset remains unchanged. It is not a migration-freshness test. Its past scores do not establish success on this contract. The older [bitemporal specification](temporal-spec.md) remains a secondary gap inventory, not the primary promotion gate.

## Human grading and promotion criteria

Prioritize these failures, in order:

1. **Stale answers:** an old value wins despite a supported completed switch or rollback.
2. **False switches:** a plan, trial, unrelated scope, or late historical capture activates a value incorrectly.
3. **Scope errors:** project, setting, or environment boundaries are crossed.
4. **Unsupported provenance:** the value is correct by chance but cited evidence does not establish the target state.
5. **Coverage:** answerable stable-state cases abstain merely because records are old; unresolved conflicts instead require honest uncertainty.

Use all 14 cases as the denominator, including errors, abstentions, and incomplete trials. An unresolved conflict must not choose an arbitrary active value; explicit uncertainty or abstention is acceptable. An explanatory answer should cite the conflicting receipts. Confidence is not correctness and no LLM judge is introduced.

Do not promote an architecture with stale answers, false activations, or scope transfers in these core controls. Require supported provenance, stable-old-fact coverage, completed-rollback handling, and uncertainty on the conflict case. Report repeated/order-control results separately. Passing a small synthetic set is necessary evidence, not proof of production correctness; follow it with held-out private source-ingestion trials and human review.

Until those trials actually run, report **unrun**, not passing. Fixture validity tests only establish input isolation, reproducibility, chronology/frequency controls, and budget completeness. No backend behavior is fixed or certified by this change.
