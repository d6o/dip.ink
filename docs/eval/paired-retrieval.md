# Offline paired wiki/hybrid comparison

This is experiment support, not a production retrieval mode. Production remains hybrid. Guard review is a separate experiment: do not change prompts or validators during this comparison.

`server/paired_retrieval_eval.py` accepts frozen packet pairs and a complete frozen wiki corpus. It never opens a graph connection or refreshes an index. Session A owns private capture, provider configuration, execution, and human adjudication.

## Inputs

Use an isolated process with `server/` on `PYTHONPATH`. Shared validator imports require the normal server environment; harmless database placeholders suffice for offline replay. Configure the caller-owned completion client separately. The module does not open a database connection. Supply:

- The full private question for each case, with a unique case ID.
- The same precomputed query vector for both retrieval arms, its canonical JSON hash, and its embedding-model identity.
- A complete coherent document corpus, **not a reviewed/gold source subset**. Each document has `name`, `body`, `vector`, and `embedding_model`.
- The corpus snapshot version and an explicit, predeclared list of evaluation/diagnostic artifact source identities to exclude.
- Frozen `hybrid` and `wiki_only` assembler packets, before subject/temporal pruning. Capture hybrid with the same query vector and corpus snapshot; a hash alone cannot prove how an external capture was made.

The existing full-question dataset may carry expected claims for later review. The harness does not pass those fields to retrieval or the answer model. Never derive candidate source lists from gold citations.

Each case has this shape:

```python
case = {
    "id": case_id,
    "question": full_question,
    "embedding_model": embedding_model,
    "query_vector": vector,
    "query_vector_sha256": hash_json(vector),
    "packets": {"hybrid": frozen_hybrid, "wiki_only": frozen_wiki},
    "retrieval_ms": {"hybrid": measured_hybrid_ms, "wiki_only": measured_wiki_ms},
}
```

`retrieval_ms` is optional. Missing timing stays unknown, not zero. Measure actual capture separately; replay does not measure graph retrieval latency.

## Wiki-only reference capture

```python
from paired_retrieval_eval import wiki_packet

frozen_wiki = wiki_packet(full_question, vector, documents,
                         embedding_model=embedding_model, k=8)
```

This uses an exact cosine query over the supplied document vectors and the shared bounded excerpt helper. It makes zero graph, embedding, or model requests. It is a reference ranking implementation, not a benchmark of the optimized production wiki index. For latency comparisons, capture from an isolated read-only index with the same vectors and record those measured times.

## Shared experiment policy

- Apply the same explicit exclusions to both frozen packets. Remove sources outside the declared corpus as well.
- Exclusions happen after candidate selection, with **no refill in either arm**. Report excluded identities and missing-corpus sources. This cannot undo ranking displacement by excluded artifacts.
- Unattributed entity summaries are omitted from both arms: their contributing source identities are unavailable for safe exclusion.
- Rebuild already-readable source excerpts from the same frozen source bodies with the shared excerpt helper. Names-only candidates stay names-only. This controls excerpt selection as well as the corpus; frozen graph facts remain unchanged until the cap.
- Apply the same 20,000-character readable-evidence cap and 2,500-character per-item cap. Here the shared cap includes fact text as well as source bodies. Metadata is separate.
- Run the existing subject, current-state, qualifier, provenance, date, project, and snapshot checks. Recompute eligibility; do not trust caller-supplied eligibility or expected-answer fields.
- Reconstruct credential-method validation context from the same frozen corpus. Complete validation bodies stay out of model input, as in production.
- Use the exact production system/user prompt, one explicit answer model, temperature zero, 400 output tokens, and a fixed seed. No fallback or automatic retry occurs.
- Alternate arm order deterministically by case index. Provider nondeterminism, batching, and external state can still matter.

Exclusion is identity-based. It cannot remove an artifact quote inside an otherwise retained note, or reliably identify artifact influence in a fact attributed to another source. Review the frozen evidence for those cases. Do not rewrite the corpus or add a lexical classifier to hide contamination.

## Private paired capture

```python
from paired_retrieval_eval import AnswerSettings, run_pairs

# Configure the caller-owned client with retries and request/response logging disabled.
async def complete(**request):
    response = await client.chat.completions.create(**request)
    choice = response.choices[0]
    return {"content": choice.message.content or "", "model": response.model,
            "finish_reason": choice.finish_reason,
            "usage": response.usage.model_dump() if response.usage else {}}

summary = await run_pairs(cases, documents, embedding_model=embedding_model,
    settings=AnswerSettings(model=pinned_answer_model), complete=complete,
    output=private_output_path, exclusions=artifact_source_ids,
    corpus_version=snapshot_version)
```

A provider that rejects the fixed settings produces recorded errors; the harness does not silently change settings. `model_calls` counts callback invocations; verify SDK retries are disabled before treating it as an HTTP request count. Reported model differences/overrides are visible in receipts and the returned summary. Do not treat such pairs as a controlled same-model comparison.

The new JSONL file uses exclusive creation and mode 0600. It stores model packets, complete raw completion text, validated results, settings, exclusions, hashes, timings, and bounded token counts. It records every case/arm, including abstentions and errors. An interrupted file has fewer receipts than its declared denominator and is incomplete. The module prints no private data.

Input, corpus, prompt, validator, runner, and packet hashes identify the compared material. Confidence and validator acceptance are not correctness. Use human adjudication with complete source evidence and all cases in the denominator. No automatic LLM judge is added. Keep private datasets and artifacts outside git. Use vault references, never credential values, in corpus fixtures or request metadata.
