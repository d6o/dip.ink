# Lane B — curator/template hardening notes

## Summary

**IN PROGRESS.** Implementing approved hardening items in order: 2 → 5 → 3 → 13 → curator/workflow portions of 14–15.

## Status

- Item 2 — complete. Removed curator scanning/redaction instructions and the secret-related review bucket while preserving the hard capture boundary.
- Item 5 — pending.
- Item 3 — pending.
- Item 13 — pending.
- Items 14–15 (curator/workflow/runner scope) — pending.

## Design decisions

- The remaining security contract is intentionally simple: note authors never submit credentials, tokens, or passwords and record only secret-manager paths. The curator does not attempt post-commit detection or repair.

## Dependencies added

- None.

## Tests and validation

- Item 2 targeted grep: no `secret scan`, `redact`, `Secrets routed`, `secrets-to-rotate`, or `literal-prefix` instructions remain in `template/AGENTS.md`, `template/.pi/prompts/**`, `template/wiki/**`, `template/notes/**`, or template workflow prose.

## Failures / deferred work

- Root and non-template documentation is owned by Lane D. Any matching documentation cleanup outside Lane B ownership is deferred to the coordinator/Lane D.

## Coordinator TODOs

- Tag `v0.1.0` only after merged CI is green, per the approved release sequence.
