# Structured ingest and recoverable quarantine (v0.1.56)

## Answer checks

The six post-v0.1.46 lexical guards are removed: temporal-negative, resource-scope, network-vantage, release-scope, storage-authority, and sandbox-contract.

Older provenance, readable-evidence, subject, date, project, temporal-state, observation, and credential-method checks remain. No general LLM judge is added. The synthetic replay preserves complete fixed questions and drafts; it is not the deleted historical evaluation dataset.

## Extraction settings

These settings apply to the custom OpenAI-compatible extraction client selected by `LLM_BASE_URL`:

| Variable | Default | Meaning |
|---|---|---|
| `LLM_STRUCTURED_OUTPUT_MODE` | `json_object` | Either `json_object` or `json_schema`; invalid values fail configuration. |
| `INGEST_STRUCTURED_RETRIES` | `1` | Corrective retries after the first response; permitted range 0–3. |
| `INGEST_MAX_FAILURES` | `3` | Failed note attempts before quarantine; permitted range 1–20. |

**Operator rollout:** set `LLM_STRUCTURED_OUTPUT_MODE=json_schema` on the batch-router ingest workload. Retain the compatible public default for providers without schema support. No rollout occurs through these code changes alone.

Both modes validate the parsed response with the requested Pydantic model before accepting it. Existing bounded envelope compatibility remains. A malformed, empty, schema-invalid, or explicitly truncated response gets at most the configured corrective retries on that model attempt. Terminal structured errors bypass Graphiti's blind JSON retries. Existing provider outage and episode transport retry policies remain bounded and unchanged.

Correction requests include parser positions/messages or schema error types/counts, plus at most 16,000 characters from the previous response. The request labels that response as untrusted data and prohibits instructions from it. The original task remains authoritative. Raw source text and model output do not enter error logs, traces, metrics, or failure-state records.

`json_invalid` means a JSON parse failed. It does not imply truncation. Only `finish_reason=length` establishes `output_truncated`, including when the returned JSON happens to parse. `llm_truncated` counts those responses. A `finish_reason=stop` parse failure remains malformed JSON, even when the parser reports an unterminated string or an expected delimiter.

## Persistent failure state

A separate Neo4j label, `DipinkIngestFailure`, stores group, slug, content hash, failure count, category, and timestamps. It stores no source or model bodies. Ingest setup creates an idempotent unique-key constraint. Status reads do not create schema or update records.

After three failed attempts by default, the scheduler excludes that unchanged note from retryable work. It remains **pending**, not done. Other notes can proceed. Partial episodes with entity mentions cannot masquerade as successful legacy ingestion when a failure record exists.

- Changed content automatically becomes retryable. A new failed content hash starts at one.
- An explicit retry resets the failure count, but retains the incomplete-attempt marker until success.
- Successful ingest marks the episode complete with its exact body hash and clears failure state.
- Cancellation does not consume the failure budget.
- Provider failures also count once their existing bounded retries exhaust. A recovered provider may require an explicit retry for quarantined notes.
- Failure-state persistence errors are logged by exception type. Database unavailability can prevent durable suppression; it never marks the note complete.

Source files are never moved, edited, or deleted by quarantine. This graph-ingest quarantine is separate from the curator's `notes/.blocked/` queue.

## Status and recovery

`INGEST_MODE=status` reports pending, retryable, quarantined, and bounded quarantine slugs. `/api/status` and `memory_status` expose retryable/quarantined counts. Prometheus exposes `dipink_ingest_quarantined_notes`. Pending lag still includes quarantined notes, so existing backlog alerts remain active.

Use the existing database environment on an explicitly authorized one-off job:

```sh
INGEST_MODE=status python ingest.py
# Reset one failure budget, scoped to GROUP_ID. This does not run extraction.
GROUP_ID=main INGEST_MODE=retry \
  INGEST_RETRY_SLUG=2026-01-01-000000-example python ingest.py
```

The next normal ingest tick retries the note. Do not leave a retry reset command in the scheduler. Inspect sanitized `INGEST_METRIC` records and the provider configuration before resetting the budget. Credentials remain in the deployment's secret store, never command arguments or notes.
