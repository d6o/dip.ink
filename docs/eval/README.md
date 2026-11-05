# Reproducible answer evaluation

## Primary product benchmark: present-state freshness

Use the [present-state contract](present-state-spec.md) to test whether the latest supported configuration beats repeated old evidence without special question wording. Its compact generator provides 14 scenarios with raw source inputs and separate grader metadata. Backend trials are unrun; fixture checks are not architecture results.

## Existing lookup and reader diagnostics (unchanged)

The [versioned dataset](../../server/evaluation_cases/synthetic-memory-v1.json) contains 30 synthetic cases across temporal changes, similar projects, absent facts, and new notes. Its [builder](../../server/evaluation_cases/make_synthetic.py) is deterministic and uses no private input.

Each case records a full question, expected claims, forbidden claims, and reviewed source slugs/dates. Public fixtures also include synthetic source bodies, a fixed draft, a packet, and an expected validator action.

### Deterministic validator checks

```sh
OPENAI_API_KEY=test-only NEO4J_PASSWORD=test-only WIKI_MCP_BACKGROUND_REINDEX=0 \
  python server/evaluate_answers.py fixture server/evaluation_cases/synthetic-memory-v1.json \
  --output /tmp/fixture-review.jsonl --label v0.1.57
```

This checks deterministic validation of fixed evidence and drafts. It does not test retrieval, ingest, a provider, or production answer correctness. The fixture includes abstentions; a fixture pass means the expected validator behavior occurred.

### Live end-to-end capture

Prepare a private dataset with the same schema and `synthetic: false`. Keep it outside this repository. Review complete questions, claims, source identities, and dates before the run. Private live cases do not need `draft`, `packet`, or `expected_action`.

```sh
python server/evaluate_answers.py live /private/path/questions.json \
  --endpoint https://your-memory-host/mcp --output /private/path/run-v0157.jsonl \
  --label v0.1.57 --timeout 600
```

The runner calls only `graph_answer` through MCP, sequentially, without retries or write tools. Questions travel in request bodies, not URLs. Synthetic live runs require `--allow-synthetic-live` and an independently seeded isolated instance; never seed production from the fixture dataset.

The artifact contains full questions, complete responses, reviewed evidence, timings, and review hints. It uses exclusive creation and mode 0600. Console output contains counts and hashes only. Do not commit private datasets or captures. Do not include credentials in datasets, labels, or endpoint URLs.

Dataset hashes cover canonical JSON, including full questions, source dates, and expected claims. A runner hash identifies the exact runner source. A unique run ID binds later reviews to one capture. Preserve these fields when comparing versions. Labels are operator-supplied descriptions, not verified server versions.

### Human review and strict denominators

Literal expected/forbidden-claim hits and citation membership are hints, **not accuracy or entailment judgments**. Confidence is not correctness. There is no automatic LLM judge.

Review complete evidence and responses. A review file has this shape:

```json
{"dataset_sha256":"<capture hash>","run_id":"<capture run ID>","verdicts":{"case-id":"supported"}}
```

Permitted verdicts are `supported`, `unsupported`, and `uncertain`. Review abstentions explicitly against the expected evidence. Errors cannot count as supported answers.

```sh
python server/evaluate_answers.py summarize /private/path/run-v0157.jsonl \
  --reviews /private/path/review-v0157.json
```

All cases remain in the denominator: answers, abstentions, errors, missing receipts, and unreviewed cases. The report gives the human-supported fraction of **all** planned cases, not only answered cases. A partial capture reports missing receipts. It cannot masquerade as a complete run.

Do not claim causal improvement from independently distilled arms. Keep the same full questions, source snapshot, reviewed expectations, and configuration. Cache state, provider/model changes, ingest activity, and sampling affect results. Record these conditions separately. The runner does not bypass caches, modify source data, or reconstruct truncated historical questions.

## Paired retrieval experiment

Use the [offline paired wiki/hybrid guide](paired-retrieval.md) to compare frozen retrieval packets with identical answer settings and shared validation. This is separate from guard review and does not change production retrieval.

## Controlled follow-up experiments

The [offline keyword/ranker/reader guide](offline-keyword-ranker-reader.md) adds SQLite FTS5 candidate experiments and explicit reader comparisons on frozen evidence. The [temporal specification](temporal-spec.md) records unsupported bitemporal query semantics as gaps, not API successes.

## Retained historical experiments

- [2026-10-11 vector recall study](2026-10-11-ingest-vector-recall.md): operation-level retrieval study, still referenced by the ingest adapter and vector operations guide.
- [2026-10-11 resolve-edge model study](2026-10-11-resolve-edge-model-test.md): historical extraction experiment with documented manual scripts and abstract result artifacts.

These are historical evidence, not current answer-accuracy benchmarks. Their references justify retention. No experiment is deleted or archived merely because it has no scheduled job. See the [tracked-reference audit](../v0.1.57-reference-audit.md).
