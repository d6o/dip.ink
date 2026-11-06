# Synthetic temporal evaluation specification

This is a secondary research/gap specification. The primary product promotion contract is now [present-state freshness](present-state-spec.md), not historical point-in-time querying. The fixture and its existing gap labels remain unchanged.

The [fixture](../../server/evaluation_cases/temporal-v1.json) contains ten synthetic query expectations. It covers supersession, late corrections, late-arrival notes, future validity starts/ends, interval boundaries, and missing validity metadata.

This is a specification and gap inventory, **not a passing live benchmark**. Every case has `api_status: gap`. Fixture consistency tests validate an independent reference calculation; they do not certify current memory API semantics.

## Two independent time dimensions

- **Valid time:** when a claim applies to the world.
- **Known/system time:** when that version of the claim is available to the system.

The fixture uses explicit versioned rows with half-open intervals: `[valid_at, invalid_at)` and `[known_at, known_until)`. A late correction can change the answer for an earlier valid-time point without changing what the system knew before the correction arrived. Multiple row versions are deliberate; this is not a claim that the current Graphiti schema stores those exact versions.

Unknown `valid_at` is not assumed to mean an unbounded past. That fixture's expected result is unknown (`null`), distinct from a known empty result (`[]`).

## Current implementation gaps

- The fixture's diagnostic explanations describe v0.1.60. v0.1.61 now evaluates known validity bounds at request time, so future starts/ends no longer use presence alone.
- Missing validity bounds retain legacy open-bound behavior; that compatibility flag is not proof of validity. Malformed nonempty bounds fail closed.
- v0.1.61 removes the 24-hour source-age eligibility cut. Source dates remain provenance, not effective or transaction time.
- The current public API does not expose a full `valid_as_of` plus `known_as_of` query contract.
- Supersession and expiry metadata are useful, but their presence/counts do not prove correct reconstruction of prior knowledge states.

A passing test that documents those limitations must not turn a gap into a temporal benchmark pass. Future evaluation should report supported, unsupported, unknown, and incorrect outcomes separately. Do not route these cases through a natural-language reader and infer temporal correctness from confidence.

## Future read-only evaluation protocol

1. Define the intended valid-time and known-time query semantics explicitly.
2. Capture versioned synthetic history in an isolated fixture, not production.
3. Compare API results with the fixture's reviewed expectations at each time boundary.
4. Preserve unsupported cases in the denominator/report instead of silently skipping them.
5. Review late corrections and unknown timestamps separately from ordinary current-state retrieval.

No Graphiti facts, validity fields, expiry fields, or production data change in this task. This specification does not justify removing the temporal graph or forcing reingest.
