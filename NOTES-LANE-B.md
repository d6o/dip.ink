# Lane B — curator/template hardening notes

## Summary

**IN PROGRESS.** Implementing approved hardening items in order: 2 → 5 → 3 → 13 → curator/workflow portions of 14–15.

## Status

- Item 2 — complete. Removed curator scanning/redaction instructions and the secret-related review bucket while preserving the hard capture boundary.
- Item 5 — complete. Added terminal `notes/.blocked/`, exact-log dedup detection, byte-preserving quarantine receipts, batching exclusions, and regression coverage for later-note progress.
- Item 3 — pending.
- Item 13 — pending.
- Items 14–15 (curator/workflow/runner scope) — pending.

## Design decisions

- The remaining security contract is intentionally simple: note authors never submit credentials, tokens, or passwords and record only secret-manager paths. The curator does not attempt post-commit detection or repair.
- Blocked receipt contract: `notes/.blocked/<slug>/BLOCKED.md` has frontmatter `schema-version: 1`, `slug`, enum `reason`, `blocked-at`, and `source-path`. The helper adds only this receipt after moving the complete folder; every pre-existing file is byte-preserved.
- Exact prior-ingest detection is centralized in `scripts/processnotes-is-ingested.py`, which searches only `ingest |` / `auto-ingest |` log sections and uses slug boundaries to avoid prefix collisions.

## Dependencies added

- None.

## Tests and validation

- Item 2 targeted grep: no `secret scan`, `redact`, `Secrets routed`, `secrets-to-rotate`, or `literal-prefix` instructions remain in `template/AGENTS.md`, `template/.pi/prompts/**`, `template/wiki/**`, `template/notes/**`, or template workflow prose.
- `bash template/scripts/test-processnotes-supervisor.sh` — pass after item 5 expansion; covers blocked byte preservation/receipt, blocked batching exclusion, exact dedup (including archived logs and prefix collision), duplicate terminal handling, and later-note progress in addition to existing supervisor cases.
- `bash -n` on all modified/new curator shell scripts — pass.
- `python3 -m py_compile template/scripts/processnotes-is-ingested.py` — pass.

## Failures / deferred work

- Root and non-template documentation is owned by Lane D. Any matching documentation cleanup outside Lane B ownership is deferred to the coordinator/Lane D.

## Coordinator TODOs

- Lane A/status integration should parse bounded blocked receipts at `notes/.blocked/*/BLOCKED.md` using the receipt contract above for count, oldest age, slug, and reason.
- Tag `v0.1.0` only after merged CI is green, per the approved release sequence.
