# resolve_edge on a faster model: a measured decision test

Date: 2026-10-11/12. Evaluator: agent `resolve-edge-model-test` (read-only).
System: memory image v0.1.26 to v0.1.27 (the rollout happened during the test), graphiti-core 0.29.2, group `mykg` (about 185,000 RELATES_TO edges).
Baseline: the cliproxy batch-router. It answered every call with `gpt-6.1-sol`.

## Summary

No tested model gives the same resolve_edge decisions as the current model with a useful speed gain. Keep the current model for `dedupe_edges.resolve_edge`.

- The baseline does not agree fully with itself. Two baseline runs on the same 180 live cases agree on 92.8% of the cases. The differences are in long contradiction lists.
- `gpt-6.1-sol` with `reasoning_effort=low` (sol-low) agrees on 90.0% of the cases. On the 167 cases where the baseline is stable, sol-low agrees on 93.4%. All its differences on these cases go in one direction: it misses 21 contradictions and adds 0. These 11 cases are 18% of the 62 stable cases with a positive decision. In the judged sample, 7 of 18 disagreements were real misses by sol-low. It is only 12% faster per call (mean 3.57 s against 4.04 s).
- `gpt-6-luna` low (luna-low) is 2.4 times faster per call (p50 1.55 s). It agrees on only 55.0% of the cases. It misses 81 and adds 73 contradiction decisions on the stable cases, and it merges 19 facts that the baseline keeps apart.
- `claude-haiku-5-5` agrees on 74.4%. `claude-haiku-4-5` is the fastest (p50 0.85 s), but it agrees on 23.5% and fails the labeled "key difference" test (9 of 20 correct). `gpt-6-astra` low agrees on 88.3%, misses 42 contradictions on the stable cases, and is not faster.
- All models find the 30 labeled true duplicates. Thus the duplicate half of the decision is easy. The contradiction half is where the fast models fail.
- The possible time gain is small. resolve_edge calls of one note run in parallel (Graphiti `semaphore_gather`, 20 slots). The 49% share is a share of summed call seconds, not of note wall time. The estimated resolve_edge wall time is about 13 s of a 204 s median note. The fastest acceptable-quality option (sol-low) saves about 4 s per note (2%). luna-low would save about 9 s per note (4%) at a large quality loss.

The operator rule is "a speed change must not reduce quality". sol-low reduces the invalidation of stale facts, and the faster models change many decisions. Thus no candidate passes the rule.

## 1. The prompt and the code path (graphiti-core 0.29.2)

Source: `/usr/local/lib/python3.12/site-packages/graphiti_core` in the memory pod.

- `utils/maintenance/edge_operations.py` `resolve_extracted_edges` makes, for each extracted edge:
  1. related edges: `EntityEdge.get_between_nodes(source, target)` (same direction), then `search(fact, EDGE_HYBRID_SEARCH_RRF, SearchFilters(edge_uuids=...))`. This is BM25 plus cosine (min score 0.6), 20 per branch, RRF, top 10.
  2. invalidation candidates: the same hybrid search over the whole group, top 10, minus the related edges.
  3. one `resolve_extracted_edge` coroutine for each edge, all through `semaphore_gather` (`SEMAPHORE_LIMIT` 20; the CronJob does not set it).
- `resolve_extracted_edge` does not call the LLM when both lists are empty, or when a related edge has the same endpoints and the same normalized fact text (fast path).
- The prompt context has three fields only: `existing_edges` (`[{idx, fact}]`), `edge_invalidation_candidates` (`[{idx, fact}]`, idx continues after the related edges), and `new_edge` (fact text). **The prompt has no episode content** in 0.29.2. Thus the test does not send episode content.
- Response model `EdgeDuplicate`: `duplicate_facts: list[int]` and `contradicted_facts: list[int]`. There is no `fact_type` field.
- The call uses `model_size=ModelSize.small`, `prompt_name='dedupe_edges.resolve_edge'`.
- Index validation: Graphiti keeps duplicate idx only in `[0, len(related))`, and contradicted idx only in `[0, len(related)+len(candidates))`. It logs a warning for other values and drops them. The first valid duplicate becomes the resolved edge. Contradicted edges go to `resolve_edge_contradictions`, which invalidates them only when their `valid_at` is before the new edge's `valid_at`.
- dip.ink `CompactSchemaClient` (server/ingest.py) appends the compact example shape `{"duplicate_facts": [0], "contradicted_facts": [0]}` to the user message, and Graphiti appends the language instruction to the system message. The request uses `temperature=1`, `max_tokens=16384`, `response_format={"type":"json_object"}`. `_generate_response` receives `model_size` but does not use it.
- `ModelSize.small` is also used by `extract_nodes.extract_summaries_batch` (mean 23.6 s, large output), `extract_edges.extract_timestamps(_batch)`, and the attribute prompts. Thus a route by `model_size` alone also moves the summary prompt.

## 2. Dataset

Scripts: `docs/eval/scripts/resolve_edge_build.py` and `resolve_edge_synth.py`. They ran in the memory pod and used Graphiti's own search functions and the ingest vector adapter (`INGEST_VECTOR_SEARCH=1`, overfetch 100), like the ingest CronJob.

For each case, the script takes an edge that is in the graph as the "extracted" fact. It rebuilds the two lists **as of the start of the edge's source episode**:

- It removes the edge itself and every edge with `created_at` at or after the episode's `created_at`. Thus other edges of the same note and later edges are not in the lists.
- Each search branch fetches 120 results, then the filter applies, then each branch is cut to 20 before RRF. This gives the result that the search had at that time, except for small BM25 statistics changes.
- The query vector is the stored `fact_embedding` of the edge (Graphiti embeds the same text).
- The script keeps only cases where Graphiti calls the LLM (no fast path, at least one list not empty).

Groups (edges created in the last 3 days, one episode each):

| group | n | selection | what it covers |
|---|---|---|---|
| R | 80 | random recent edges | the normal mix (mean 0.65 related edges, 9.8 candidates) |
| C | 50 | edges that Graphiti used to invalidate an older edge X: `X.invalid_at = Y.valid_at`, Y created less than 2 h before `X.expired_at`, X and Y share an entity, X is in the rebuilt lists | positive contradiction decisions |
| D | 50 | edges with an older near-duplicate between the same two entities (other episode, Neo4j cosine score at least 0.85) | duplicate boundary cases (mean 4.5 related edges) |
| S | 30 | synthetic: new fact = meaning-preserving rewrite of a real related edge T | labeled true duplicates |
| K | 20 | synthetic: new fact = T with one key detail changed (number, date, version, name) | labeled "key difference, not a duplicate" |

A live graph has almost no true duplicate cases: when Graphiti merges an extracted fact, no edge stays behind. For this reason the S and K groups exist. `claude-opus-5-5` wrote the S and K facts. It is not one of the tested models. The script embedded each new fact with the Graphiti embedder and rebuilt both lists for it.

Prompt size: mean 1,518 input tokens on base1, close to production (1,471).

## 3. Model configurations

All calls used the rendered ingest request (section 1). Only the endpoint, the model, and `reasoning_effort` changed. Concurrency was 3. Calls for one case ran next to each other, so all configurations saw the same time of day.

| config | endpoint | model | extra |
|---|---|---|---|
| base1, base2 | `batch-router.cliproxy:8320` | `batch-router` (answered as gpt-6.1-sol, 100%) | - |
| sol-low | `cliproxy:8317` | `gpt-6.1-sol` | `reasoning_effort=low` |
| astra-low | `cliproxy:8317` | `gpt-6-astra` | `reasoning_effort=low` |
| luna-low | `cliproxy:8317` | `gpt-6-luna` | `reasoning_effort=low` (0 reasoning tokens) |
| haiku | `cliproxy:8317` | `claude-haiku-5-5` | - |
| haiku45 | `cliproxy:8317` | `claude-haiku-4-5-20251001` | - |

Not tested, because the provider refused the calls on 2026-10-11: `glm`, `glm-5.3-flash` (HTTP 429, no balance), `gemini-3-flash` (HTTP 429, quota resets in about 74 h), `grok-4.7-build-fast` (HTTP 402, balance exhausted). `gpt-6-luna` without `reasoning_effort` used about 240 reasoning tokens and took about 5.5 s; thus only the low setting is useful. `reasoning_effort=none` on gpt-6.1-sol was accepted but still produced reasoning tokens.

Comparison rule: "effective" decisions, after Graphiti's index validation (section 1). Two answers agree when the duplicate set and the contradicted set are both equal.

## 4. Results on the 180 live cases

| config | p50 s | p90 s | mean s | reasoning tok | agree with base1 | agree when base is stable (167) | missed / extra contra (stable) | extra dup (stable) | baseline-positive | baseline-negative |
|---|---|---|---|---|---|---|---|---|---|---|
| base2 | 3.25 | 6.35 | 3.81 | 42 | 92.8% | - | - | - | 87.3% | 96.3% |
| sol-low | 2.81 | 6.14 | 3.57 | 27 | 90.0% | 93.4% | 21 / 0 | 0 | 76.1% | 99.1% |
| astra-low | 2.83 | 6.28 | 4.04 | 20 | 88.3% | 91.6% | 42 / 2 | 1 | 73.2% | 98.2% |
| haiku | 2.69 | 4.43 | 2.84 | 0 | 74.4% | 77.2% | 44 / 35 | 12 | 56.3% | 86.2% |
| luna-low | 1.55 | 2.15 | 1.67 | 0 | 55.0% | 57.5% | 81 / 73 | 19 | 22.5% | 76.1% |
| haiku45 | 0.85 | 1.45 | 1.06 | 0 | 23.5% | 24.1% | 99 / 172 | 65 | 18.6% | 26.6% |

base1: p50 3.23 s, p90 7.18 s, mean 4.27 s, 45 reasoning tokens of 66 output tokens.
"Baseline-positive" = base1 returned at least one idx (71 cases). "Baseline-negative" = base1 returned two empty lists (109 cases).
JSON: all configurations returned valid JSON in all cases, except haiku45 (1 JSON error, 5 out-of-range idx values that Graphiti drops).

Labeled synthetic cases (S: 30 true duplicates, K: 20 key differences):

| config | S: T marked duplicate | S: wrong extra duplicate | K: T not marked duplicate | K: T contradicted | agree with base1 (50) |
|---|---|---|---|---|---|
| base1 | 30/30 | 0 | 20/20 | 19/20 | - |
| base2 | 30/30 | 0 | 20/20 | 19/20 | 94% |
| sol-low | 30/30 | 0 | 20/20 | 19/20 | 94% |
| astra-low | 30/30 | 0 | 20/20 | 19/20 | 92% |
| haiku | 30/30 | 0 | 20/20 | 18/20 | 76% |
| luna-low | 30/30 | 2 | 20/20 | 19/20 | 72% |
| haiku45 | 30/30 | 4 | 9/20 | 18/20 | 50% |

The full generated tables (by group, index-level differences, decision rates) are in appendix A and B.

## 5. Judged disagreements (22 cases)

Method: I read the new fact and each disputed fact, and applied the rules of the prompt. Duplicate = identical factual information. Contradiction = the new fact updates, supersedes, or is incompatible with the old fact. A newer measurement, version, state, or count of the same thing is an update. Verdicts: "baseline" (base1 is right), "candidate", or "unclear" (both answers are defensible). I judged all 18 sol-low disagreements and a random 4 of the 81 luna-low disagreements. The per-case reasons are in `docs/eval/data/2026-10-11-resolve-edge-judgments.json`. The reasons are abstract, because the facts come from the private graph.

| config | judged | baseline right | candidate right | unclear |
|---|---|---|---|---|
| sol-low | 18 | 7 | 7 | 4 |
| luna-low | 4 | 4 | 0 | 0 |

Patterns:

- In 6 of the 7 "candidate right" sol-low cases, base2 gave the same answer as sol-low. Thus these are mostly baseline sampling noise, not a better candidate.
- All 7 "baseline right" sol-low cases are missed contradictions. base2 agreed with base1 on all 7. Examples: a count of stranded drafts changed from three to four; a newer analytics snapshot for one page, where sol-low keeps two older snapshots current; a release replaced an older release, but sol-low keeps "the public pointer targets the old release"; a successful render, where sol-low keeps "the new render failed".
- luna-low misses whole groups of stale facts (for example three older image pins after a new image digest). It also marks consistent facts as contradicted (one fact that says the same thing, and one fact about a different branch).

Effect in the graph: a missed contradiction leaves a stale fact current. A search can then return two current answers for one question. An extra contradiction invalidates a valid fact. An extra duplicate drops a new fact and adds its episode to an older edge. All three types reduce answer quality.

## 6. Time per note

Production numbers (ingest metrics of 329 notes in the last 3 days): median note wall time 204 s (mean 258 s). resolve_edge has mean 20.3 calls per note and 122 s of summed call time per note. The production mean per call is about 6 s, higher than in this test (4.0 s), because 20 calls run at the same time.

The resolve_edge calls of one note run in parallel (20 slots), and the phase ends with the slowest call. A simulation with the latencies of this test (2,000 draws of a note's call count) gives:

| config | summed call s per note | wall s per note (mean) | wall s per note (p90) | wall saving |
|---|---|---|---|---|
| baseline (base1+base2) | 82 | 12.6 | 14.1 | - |
| sol-low | 73 | 8.2 | 11.1 | about 4 s (2% of a median note) |
| astra-low | 84 | 14.6 | 61.9 | none (long tail) |
| haiku | 58 | 6.2 | 8.6 | about 6 s |
| luna-low | 35 | 3.2 | 4.6 | about 9 s (4%) |
| haiku45 | 21 | 3.3 | 6.0 | about 9 s |

The simulation ignores the `extract_timestamps` call (mean 3.3 s) that follows each new edge in the same coroutine, and the slowdown from 20 parallel calls. Each call also has a fixed cost: a trivial `{"ok": true}` request took about 2.2 s through the proxy. The serial prompts limit the note wall time: `extract_edges.edge` (mean 65 s), `extract_nodes.extract_summaries_batch` (24 s), `extract_nodes.extract_text` (20 s), `dedupe_nodes.nodes` (11 s), and the Neo4j cosine scans.

## 7. Recommendation

1. Keep `dedupe_edges.resolve_edge` on the current model (batch-router, now gpt-6.1-sol at default effort). No candidate passes the rule "no quality loss":
   - sol-low changes 11 of the 62 stable baseline-positive cases (18%). Each change removes one or more contradictions (21 idx in total, 0 added). Of these 11 cases, I judged 7 as real misses, 1 as a correct change, and 3 as unclear. The gain is about 4 s per note.
   - luna-low, haiku, and haiku45 change 25% to 77% of the decisions.
   - astra-low misses 42 contradictions on the stable cases and is not faster.
2. Do not route "all small prompts" by `model_size`. `ModelSize.small` also covers `extract_summaries_batch`, which is a large prompt. This test did not measure it.
3. If a speed gain is necessary later, these steps are more useful than a model change for resolve_edge:
   - Measure the serial prompts (`extract_edges.edge`, `extract_summaries_batch`), because they set the note wall time.
   - Do this test again when `gemini-3-flash`, `glm-5.3-flash`, or a `grok` fast model have quota. The scripts can run again without changes (add a config line).

Code change if the lead decides to route anyway: `CompactSchemaClient.generate_response` receives `prompt_name` and `model_size`, but `_generate_response` uses one ladder for all prompts. Add a second ladder (for example `LLM_FAST_MODEL_LADDER` and an allow-list `LLM_FAST_PROMPTS=dedupe_edges.resolve_edge`), select it by `prompt_name` (not by `model_size`), and pass `reasoning_effort` through `extra_body` when the ladder entry needs it. Record the routed model in `ingest_metrics` (this already works through `response.model`).

## 8. Limits

- One evaluator judged the disagreements. The sample is 22 cases. The judged result for sol-low (7 against 7, plus 4 unclear) is not a statistical proof. The direction of the error (21 missed, 0 extra, on stable cases) is the stronger signal.
- The baseline is stochastic at `temperature=1`. Two runs are not enough to know the "majority" answer for each case. The "stable" subset (base1 = base2) is a practical filter.
- The live sample covers 3 days of notes. The note mix is mostly operational and SEO run logs.
- The S and K facts are synthetic. They test the duplicate rule only.
- The C-group link (X to Y) is a heuristic. The baseline re-found the historic invalidated edge in 60% of the C cases. The historic decision came from the model that ingested the note at that time.
- Latency was measured at concurrency 3, not at the production concurrency of 20.

## 9. Reproduce

The scripts are in `docs/eval/scripts/`. The data without private text is `docs/eval/data/2026-10-11-resolve-edge.json.gz` (230 cases, 1,610 calls) and `docs/eval/data/2026-10-11-resolve-edge-note-calls.json` (calls per note).

```sh
P=$(kubectl -n graphiti get pod -l app=memory -o name | head -1); P=${P#pod/}
kubectl -n graphiti exec $P -- mkdir -p /tmp/ret
for f in resolve_edge_build.py resolve_edge_synth.py resolve_edge_run.py; do
  kubectl -n graphiti cp docs/eval/scripts/$f $P:/tmp/ret/$f; done
kubectl -n graphiti exec $P -- python3 /tmp/ret/resolve_edge_build.py --days 3 --r 80 --c 50 --d 50
kubectl -n graphiti exec $P -- python3 /tmp/ret/resolve_edge_synth.py --s 30 --k 20
kubectl -n graphiti exec $P -- python3 /tmp/ret/resolve_edge_run.py --cases /tmp/ret/cases.json \
  --out /tmp/ret/calls.jsonl --concurrency 3 --config base1 --config base2 --config sol-low \
  --config luna-low --config astra-low --config haiku45 --config haiku
# the same for --cases /tmp/ret/synth.json --out /tmp/ret/calls_synth.jsonl
# copy the files out, run resolve_edge_export.py, then:
python3 docs/eval/scripts/resolve_edge_analyze.py --bundle docs/eval/data/2026-10-11-resolve-edge.json.gz \
  --groups CDR --notes docs/eval/data/2026-10-11-resolve-edge-note-calls.json
python3 docs/eval/scripts/resolve_edge_analyze.py --bundle docs/eval/data/2026-10-11-resolve-edge.json.gz --groups SK
kubectl -n graphiti exec $P -- rm -rf /tmp/ret
```

The memory pod restarted once during the first run (the v0.1.27 rollout) and lost `/tmp/ret`. Copy the output files out of the pod while a run is active.

## Appendix A: generated tables, live cases (groups C, D, R)

#### Calls, validity, latency, tokens

| config | real model(s) | ok | http err | JSON/schema err | out-of-range idx | p50 s | p90 s | mean s | mean out tok | mean reasoning tok |
|---|---|---|---|---|---|---|---|---|---|---|
| base1 | gpt-6.1-sol (180) | 180 | 0 | 0 | 0 | 3.23 | 7.18 | 4.27 | 66 | 45 |
| base2 | gpt-6.1-sol (180) | 180 | 0 | 0 | 0 | 3.25 | 6.35 | 3.81 | 63 | 42 |
| astra-low | gpt-6-astra (180) | 180 | 0 | 0 | 0 | 2.83 | 6.28 | 4.04 | 41 | 20 |
| haiku | claude-haiku-5-5 (180) | 180 | 0 | 0 | 0 | 2.69 | 4.43 | 2.84 | 512 | 0 |
| haiku45 | claude-haiku-4-5-20251001 (179) | 179 | 0 | 1 | 5 | 0.85 | 1.45 | 1.06 | 27 | 0 |
| luna-low | gpt-6-luna (180) | 180 | 0 | 0 | 0 | 1.55 | 2.15 | 1.67 | 22 | 0 |
| sol-low | gpt-6.1-sol (180) | 180 | 0 | 0 | 0 | 2.81 | 6.14 | 3.57 | 47 | 27 |

#### Agreement with the baseline (base1), exact set equality of effective decisions

| config | n | both sets equal | duplicate set equal | contradicted set equal | on baseline-positive | on baseline-negative | equal to base1 OR base2 |
|---|---|---|---|---|---|---|---|
| base2 | 180 | 167 (92.8%) | 179 (99.4%) | 168 (93.3%) | 62/71 (87.3%) | 105/109 (96.3%) | - |
| astra-low | 180 | 159 (88.3%) | 178 (98.9%) | 161 (89.4%) | 52/71 (73.2%) | 107/109 (98.2%) | 165/180 (91.7%) |
| haiku | 180 | 134 (74.4%) | 172 (95.6%) | 141 (78.3%) | 40/71 (56.3%) | 94/109 (86.2%) | 138/180 (76.7%) |
| haiku45 | 179 | 42 (23.5%) | 139 (77.7%) | 66 (36.9%) | 13/70 (18.6%) | 29/109 (26.6%) | 43/179 (24.0%) |
| luna-low | 180 | 99 (55.0%) | 169 (93.9%) | 109 (60.6%) | 16/71 (22.5%) | 83/109 (76.1%) | 103/180 (57.2%) |
| sol-low | 180 | 162 (90.0%) | 178 (98.9%) | 164 (91.1%) | 54/71 (76.1%) | 108/109 (99.1%) | 168/180 (93.3%) |

#### Agreement on cases where the baseline agrees with itself (base1 = base2: 167 of 180 cases)

On these cases the baseline answer is stable, so a difference is a model difference, not sampling noise.

| config | both sets equal | missed contradicted idx | extra contradicted idx | missed dup idx | extra dup idx |
|---|---|---|---|---|---|
| astra-low | 153/167 (91.6%) | 42 | 2 | 0 | 1 |
| haiku | 129/167 (77.2%) | 44 | 35 | 0 | 12 |
| haiku45 | 40/166 (24.1%) | 99 | 172 | 0 | 65 |
| luna-low | 96/167 (57.5%) | 81 | 73 | 0 | 19 |
| sol-low | 156/167 (93.4%) | 21 | 0 | 0 | 0 |

#### Agreement with the baseline by sample group (both sets equal)

| config | C | D | R |
|---|---|---|---|
| base2 | 46/50 (92%) | 47/50 (94%) | 74/80 (92%) |
| astra-low | 41/50 (82%) | 44/50 (88%) | 74/80 (92%) |
| haiku | 34/50 (68%) | 32/50 (64%) | 68/80 (85%) |
| haiku45 | 10/50 (20%) | 3/49 (6%) | 29/80 (36%) |
| luna-low | 22/50 (44%) | 21/50 (42%) | 56/80 (70%) |
| sol-low | 42/50 (84%) | 47/50 (94%) | 73/80 (91%) |

#### Index-level differences against base1 (sum over all cases)

Extra duplicate = the model merges the new fact into an edge that the baseline keeps apart. Missed duplicate = the model keeps a new edge that the baseline merges. Extra contradiction = the model invalidates an edge that the baseline keeps current. Missed contradiction = the model keeps an edge current that the baseline invalidates.

| config | baseline dup idx | extra dup | missed dup | baseline contra idx | extra contra | missed contra |
|---|---|---|---|---|---|---|
| base1 | 2 | 0 | 0 | 244 | 0 | 0 |
| base2 | 2 | 0 | 1 | 244 | 16 | 2 |
| astra-low | 2 | 1 | 1 | 244 | 7 | 48 |
| haiku | 2 | 12 | 0 | 244 | 48 | 51 |
| haiku45 | 2 | 66 | 0 | 243 | 189 | 108 |
| luna-low | 2 | 19 | 2 | 244 | 86 | 87 |
| sol-low | 2 | 0 | 2 | 244 | 5 | 23 |

#### Decision rates (share of cases with a non-empty set)

| config | any duplicate | any contradiction | C group: historic invalidated edge in contradicted set |
|---|---|---|---|
| base1 | 2/180 (1%) | 69/180 (38%) | 30/50 (60%) |
| base2 | 1/180 (1%) | 73/180 (41%) | 30/50 (60%) |
| astra-low | 2/180 (1%) | 61/180 (34%) | 26/50 (52%) |
| haiku | 10/180 (6%) | 70/180 (39%) | 28/50 (56%) |
| haiku45 | 42/179 (23%) | 120/179 (67%) | 37/50 (74%) |
| luna-low | 9/180 (5%) | 66/180 (37%) | 26/50 (52%) |
| sol-low | 0/180 (0%) | 64/180 (36%) | 29/50 (58%) |

#### Estimated resolve_edge time per note (329 notes of the last 3 days, mean 20.3 calls per note)

Sum = total call seconds per note (what ingest_report counts). Wall = simulated semaphore_gather with 20 parallel slots, latencies drawn from this test (2,000 draws). The wall model ignores the extract_timestamps call that follows each new edge and any slowdown from 20 parallel calls.

The baseline row pools the base1 and base2 latencies.

| config | mean sum s / note | mean wall s / note | p90 wall s / note | wall saving vs baseline |
|---|---|---|---|---|
| base1 | 82 | 12.6 | 14.1 | - |
| astra-low | 84 | 14.6 | 61.9 | -2.0 s |
| haiku | 58 | 6.2 | 8.6 | +6.4 s |
| haiku45 | 21 | 3.3 | 6.0 | +9.3 s |
| luna-low | 35 | 3.2 | 4.6 | +9.4 s |
| sol-low | 73 | 8.2 | 11.1 | +4.4 s |


## Appendix B: generated tables, labeled synthetic cases (groups S, K)

#### Calls, validity, latency, tokens

| config | real model(s) | ok | http err | JSON/schema err | out-of-range idx | p50 s | p90 s | mean s | mean out tok | mean reasoning tok |
|---|---|---|---|---|---|---|---|---|---|---|
| base1 | gpt-6.1-sol (50) | 50 | 0 | 0 | 0 | 2.81 | 6.72 | 3.59 | 48 | 27 |
| base2 | gpt-6.1-sol (50) | 50 | 0 | 0 | 0 | 2.63 | 5.26 | 3.45 | 49 | 28 |
| astra-low | gpt-6-astra (50) | 50 | 0 | 0 | 0 | 2.16 | 4.91 | 2.74 | 28 | 8 |
| haiku | claude-haiku-5-5 (50) | 50 | 0 | 0 | 0 | 2.40 | 3.52 | 2.56 | 436 | 0 |
| haiku45 | claude-haiku-4-5-20251001 (50) | 50 | 0 | 0 | 0 | 0.86 | 1.39 | 0.97 | 29 | 0 |
| luna-low | gpt-6-luna (50) | 50 | 0 | 0 | 0 | 1.60 | 2.57 | 1.82 | 23 | 0 |
| sol-low | gpt-6.1-sol (50) | 50 | 0 | 0 | 0 | 2.76 | 5.51 | 3.22 | 36 | 15 |

#### Agreement with the baseline (base1), exact set equality of effective decisions

| config | n | both sets equal | duplicate set equal | contradicted set equal | on baseline-positive | on baseline-negative | equal to base1 OR base2 |
|---|---|---|---|---|---|---|---|
| base2 | 50 | 47 (94.0%) | 50 (100.0%) | 47 (94.0%) | 46/49 (93.9%) | 1/1 (100.0%) | - |
| astra-low | 50 | 46 (92.0%) | 50 (100.0%) | 46 (92.0%) | 45/49 (91.8%) | 1/1 (100.0%) | 47/50 (94.0%) |
| haiku | 50 | 38 (76.0%) | 50 (100.0%) | 38 (76.0%) | 37/49 (75.5%) | 1/1 (100.0%) | 39/50 (78.0%) |
| haiku45 | 50 | 25 (50.0%) | 35 (70.0%) | 37 (74.0%) | 24/49 (49.0%) | 1/1 (100.0%) | 25/50 (50.0%) |
| luna-low | 50 | 36 (72.0%) | 48 (96.0%) | 38 (76.0%) | 35/49 (71.4%) | 1/1 (100.0%) | 36/50 (72.0%) |
| sol-low | 50 | 47 (94.0%) | 50 (100.0%) | 47 (94.0%) | 46/49 (93.9%) | 1/1 (100.0%) | 48/50 (96.0%) |

#### Agreement on cases where the baseline agrees with itself (base1 = base2: 47 of 50 cases)

On these cases the baseline answer is stable, so a difference is a model difference, not sampling noise.

| config | both sets equal | missed contradicted idx | extra contradicted idx | missed dup idx | extra dup idx |
|---|---|---|---|---|---|
| astra-low | 44/47 (93.6%) | 12 | 0 | 0 | 0 |
| haiku | 37/47 (78.7%) | 15 | 15 | 0 | 0 |
| haiku45 | 24/47 (51.1%) | 22 | 23 | 0 | 19 |
| luna-low | 34/47 (72.3%) | 23 | 10 | 0 | 10 |
| sol-low | 45/47 (95.7%) | 1 | 1 | 0 | 0 |

#### Agreement with the baseline by sample group (both sets equal)

| config | K | S |
|---|---|---|
| base2 | 18/20 (90%) | 29/30 (97%) |
| astra-low | 18/20 (90%) | 28/30 (93%) |
| haiku | 14/20 (70%) | 24/30 (80%) |
| haiku45 | 4/20 (20%) | 21/30 (70%) |
| luna-low | 12/20 (60%) | 24/30 (80%) |
| sol-low | 18/20 (90%) | 29/30 (97%) |

#### Index-level differences against base1 (sum over all cases)

Extra duplicate = the model merges the new fact into an edge that the baseline keeps apart. Missed duplicate = the model keeps a new edge that the baseline merges. Extra contradiction = the model invalidates an edge that the baseline keeps current. Missed contradiction = the model keeps an edge current that the baseline invalidates.

| config | baseline dup idx | extra dup | missed dup | baseline contra idx | extra contra | missed contra |
|---|---|---|---|---|---|---|
| base1 | 30 | 0 | 0 | 80 | 0 | 0 |
| base2 | 30 | 0 | 0 | 80 | 2 | 7 |
| astra-low | 30 | 0 | 0 | 80 | 0 | 19 |
| haiku | 30 | 0 | 0 | 80 | 16 | 21 |
| haiku45 | 30 | 21 | 0 | 80 | 23 | 28 |
| luna-low | 30 | 10 | 0 | 80 | 14 | 24 |
| sol-low | 30 | 0 | 0 | 80 | 1 | 8 |

#### Decision rates (share of cases with a non-empty set)

| config | any duplicate | any contradiction | C group: historic invalidated edge in contradicted set |
|---|---|---|---|
| base1 | 30/50 (60%) | 23/50 (46%) | 0/0 (0%) |
| base2 | 30/50 (60%) | 24/50 (48%) | 0/0 (0%) |
| astra-low | 30/50 (60%) | 21/50 (42%) | 0/0 (0%) |
| haiku | 30/50 (60%) | 23/50 (46%) | 0/0 (0%) |
| haiku45 | 41/50 (82%) | 20/50 (40%) | 0/0 (0%) |
| luna-low | 30/50 (60%) | 20/50 (40%) | 0/0 (0%) |
| sol-low | 30/50 (60%) | 22/50 (44%) | 0/0 (0%) |

#### Labeled duplicate test (synthetic new facts from real edges T)

S = meaning-preserving rewrite of T (correct: T in duplicate_facts). K = T with one key detail changed (correct: T not in duplicate_facts; T in contradicted_facts is the expected extra answer).

| config | S: T marked duplicate | S: other extra dup idx | K: T not duplicate (correct) | K: T contradicted |
|---|---|---|---|---|
| base1 | 30/30 | 0/30 | 20/20 | 19/20 |
| base2 | 30/30 | 0/30 | 20/20 | 19/20 |
| astra-low | 30/30 | 0/30 | 20/20 | 19/20 |
| haiku | 30/30 | 0/30 | 20/20 | 18/20 |
| haiku45 | 30/30 | 4/30 | 9/20 | 18/20 |
| luna-low | 30/30 | 2/30 | 20/20 | 19/20 |
| sol-low | 30/30 | 0/30 | 20/20 | 19/20 |

