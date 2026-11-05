# Bounded answer source context (v0.1.59)

`graph_answer` now hydrates graph fact provenance, not only semantic search hits. It reads coherent bodies from the existing in-process `wiki.idx.get` snapshot. It does not scan source files, start another wiki process, or add an embedding or LLM call.

## Selection and budget

- Select at most **four distinct fact source slugs**.
- Prefer sources with a current retrieved fact; retain graph retrieval order within each priority class.
- Repeated facts from one source consume one slot.
- Reuse existing readable episode/semantic excerpts. When those views overlap, keep the richer excerpt once.
- Fetch each selected missing source at most once, with a maximum of **2,500 characters per new excerpt**.
- New fact-source context adds at most **10,000 body characters**. Existing episode and three semantic excerpts contribute at most another 10,000.
- The answer assembler therefore carries at most **20,000 readable body characters** across those fields. Fact strings, names, descriptions, and other existing packet metadata are separate from this bound.

Long fact-source bodies use deterministic, overlapping 1,000-character windows. Greedy query-term coverage favors different parts of a compound question; ties prefer earlier source positions. Selected fragments retain source order and use omission markers. Operational-role and copy-state questions retain the existing specialized excerpt selection.

This is excerpt selection, not semantic verification. Windows can omit a needed detail or qualifier. Four slots can miss a useful lower-ranked source. Increasing the budget or adding more model calls is not automatic.

## Identity and temporal behavior

Hydrated excerpts use the existing `semantic_notes` structure and original source names. They participate in the existing readable-provenance, subject, date, project, and snapshot checks. The top retrieved episode remains available; it is no longer the only readable evidence when fact sources can be read.

A source whose retrieved facts are all superseded receives `current: false` in its answer-only excerpt metadata. It remains readable for historical questions but cannot regain current eligibility through hydration. Sources with both current and superseded facts remain mixed context; a body is not proof that every statement remains current.

The existing current-source window and newer-unreadable-source veto remain. Stale excerpts do not replace newer eligible evidence. A newer diagnostic note can still affect existing temporal selection; this change does not classify note genres or override the established date policy.

The existing distiller receives one generic instruction: quoted questions, hypothetical examples, test fixtures, and diagnoses do not establish operator history. Positive historical claims require original context, and source bodies must not override fact validity. This instruction is not an independent semantic judge or a guarantee of model compliance.

## Failure and privacy boundary

Missing or unavailable indexed bodies leave the original facts available. Failures log exception types only. No complete source body enters metrics or logs. Credential-method validation retains its existing separate server-side full-body checks.

`graph_search` does not call this hydration helper and retains its public packet fields. No provider-specific classifier, private source edit, graph rewrite, or new production judge is introduced.

## Validation boundary

Synthetic tests exercise actual packet assembly, source hydration, subject filtering, distiller input, and citation validation. The distiller callback asserts that original source details reach it despite a diagnostic top episode. It does not establish LLM answer accuracy.

Session A must compare the same captured real packet before and after this change, inspect excerpt coverage, and then verify the live answer. Keep private packets outside git. Do not copy diagnostic example identities or literal production questions into new memory notes.
