# Execution-phase checkpoint

This branch is a checkpoint, not a release candidate with approval.
Do not merge it into `main` or create a v0.1.56 tag without a new review.
The user requested a clean checkpoint before the next session.

## Source state

- Public `main`: `b6068a835d212c50d1309dbe2ef59d85709b8622`.
- Published release: v0.1.55, tag object `72cac6ce4371cee57725770b478cc482559a96fe`.
- Candidate: `2f80d14d76eae8cfa92b31ab59cc01350e76937d`.
- Candidate history: `9ad9d03`, `f1c94b9`, then `2f80d14`.
- Current task: dip.ink#26, dry-run, cleanup, and proof-domain separation.
- The branch includes local v0.1.56 pins, documentation, and the existing image fixture extension.
- No v0.1.56 tag, public release, private adoption, or deployment exists from this work.

The implementation changes only these five paths:

- `server/graph.py`
- `server/Dockerfile`
- `server/execution_phase_qualifiers.py`
- `server/tests/test_execution_phase_qualifiers.py`
- `server/tests/test_graph_execution_phases.py`

The new route is `execution_phase_mismatch`.
Missing full source bodies have a separate reason.
All old guard modules and tests remain unchanged.
The image fixture includes two new modules, for 16 modules in total.
Its expected source test count is 551, not an installed runtime observation.

## Current blocker

Exact `2f80d14` still accepts these synthetic raw qualifiers after a supported dry-run record:

```text
method: "unverified direct method"
proof domain: unknown
```

```text
phase: "unverified dry-run phase"
proof domain: unknown
```

The pure helper returns no problem for both cases.
These values cannot name a valid method or phase in the canonical grammar.
They must not erase the established source scope and hide the proof qualifier.
Use role types and valid scope grammar, not a larger list of negative phrases.
Preserve valid different-method, different-phase, and different-resource controls.
Preserve literal identity data that contains words such as `unknown`.
Actual validator and current/noncurrent assembly proof remains required for these two cases.

The lead sent both cases to the implementer and reviewer before the user requested this checkpoint.
Both agents stopped for the checkpoint.
No completed independent clearance exists for `2f80d14`.
Do not reuse a stopped review as new clearance.

## Earlier corrections

The candidate includes these corrections, subject to fresh independent verification:

1. Keep formatted Go-version concepts and operative identity or limit questions active.
2. Keep reordered version-limit facets active without treating them as literal versions.
3. Recognize preview flags while keeping explicit command identity data opaque.
4. Restrict environment fields to phase-compatible conditions.
5. Prevent false executable proof and no-side-effects claims inside environment fields.
6. Preserve scope for the previously reported simple unknown annotations.
7. Bind quoted multiword roles to their own literal occurrences.
8. Bind recognized natural Go versions, direct/brokered methods, and task identities.

A dry run must not inherit actual cleanup deadlines or headroom effects.
A filesystem write check must not establish executable identity, environment, integrity, or lease.
A concurrent filesystem delta must not become exact cache size or producer attribution.
A spent selection must not become a new grant.

## Validation before the checkpoint

The lead repeated these checks on exact `2f80d14`:

| Check | Result |
|---|---|
| Full suite | 961 tests, OK; three intended native database skips |
| New phase suite | 58 tests, OK |
| Existing sandbox suite | 38 tests, OK |
| Existing storage suite | 68 tests, OK |
| Existing scope suite | 314 tests, OK |
| Query-role subset | 34 tests, included in the scope total |
| HTTP suite | Six tests, OK |
| YAML, repository contracts, and diff checks | Pass |

These passing suites do not clear the current blocker.
The implementer also reports 14 adapted independent methods and 352 successful observations.
That report is not fresh independent clearance.

All blocked software-hook counters were zero in the lead's repeated suites.
The full suite used 31 loopback DNS calls, 31 loopback connections, and 19 reverse lookups.
Other suites used no hooked socket entrypoints.
Python hooks do not prove kernel, native-code, file, or credential isolation.

## Saved review tools

`unit-runner.py` is a copy of the inspected external runner.
It sets fake configuration and restrictions before repository imports.
It disables Git global/system configuration, credential helpers, hooks, templates, and protocols.
Only the established fresh-temporary-repository Git fixtures are within the test scope.
Python hooks do not constrain native Git I/O.

`independent_phase_review.py` preserves the earlier independent f1c review fixtures.
Its output path differs from the original retained artifact path.
The file does not cover the two new multiword annotation cases above.
It must not replace a new independent review.

The runner uses the current repository directory.
It uses the existing cached dependencies at `/tmp/dipdeps` when available.
Example for the known trusted repository fixtures:

```sh
PYTHONNOUSERSITE=1 PYTHONPATH=/tmp/dipdeps /usr/bin/python3 -P \
  maintenance/checkpoints/execution-phase-20261017/unit-runner.py phase
```

Other modes are `full`, `focused`, `storage68`, `old314`, `queryroles34`, `http6`, and `independent14`.
The `focused` mode runs the existing sandbox suite, not the new phase suite.
Stop on any unexpected connection attempt.
Do not retry through another method, profile, credential, or loader.

## Resume

1. Read dip.ink#26 and this checkpoint.
2. Confirm the exact candidate and preserve all retained artifacts.
3. Correct the known scope-type defect in the five owned paths.
4. Add unsafe cases and genuine scoped positives at all actual validation stages.
5. Run the full and old suites after the last change.
6. Obtain fresh independent review on the new immutable hash.
7. Integrate only after the bounded source review passes.
8. Complete the existing public main and immutable release image procedure separately.
9. Keep live adoption blocked until an exact supported MEMORY method/resource/verb is selected.

The unavailable source body remains unavailable; do not reconstruct or fetch it.
Private source URLs and retained output paths provide no access permission.
No provider call, question replay, Go execution, cleanup, artifact access, native probe, or rollout follows from this checkpoint.
Public publication and installed fixtures are not live adoption or operational authority.
The two failed ingest windows and their separate read-only audit remain outside this source task.
