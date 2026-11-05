# Offline keyword, relevance-ranker, and reader experiments

These helpers do not change production retrieval, handlers, prompts, or Graphiti data. They use caller-supplied frozen corpora and callbacks. Session A selects approved existing models and runs private experiments. No provider is provisioned and no model call occurs automatically.

## Separate the axes

1. Compare vector top-50 candidates with vector/BM25 fusion while the relevance ranker and answer reader stay fixed.
2. Compare relevance-ranker choices on the same frozen candidates while the answer reader stays fixed.
3. Compare explicit reader settings on one identical frozen evidence packet per question.

Do not change all three at once and attribute an improvement to one factor. Expected answers and reviewed/gold source identities stay outside retrieval and relevance-ranker inputs. Confidence is not correctness; a relevance ranker is not an answer judge.

## SQLite FTS5/BM25

`server/offline_candidates.py` uses stdlib `sqlite3` with FTS5 and BM25. SQLite 3.35+ with FTS5 is required; missing support fails visibly rather than falling back to a Python text scan.

```python
from offline_candidates import KeywordIndex, select_candidates, rerank, expand_parents

with KeywordIndex(documents, index_metadata=True) as keyword:
    selection = select_candidates(question, vector, documents, keyword,
        embedding_model=embedding_model, mode="rrf", exclusions=artifact_ids)
```

Each document has the existing `name`, full `body`, document `vector`, and `embedding_model`. Optional `title`, `heading`, and `date` metadata are preserved. Missing titles use source names. Markdown headings split keyword sections, except inside fenced blocks. The full original parent remains the reader's source; sections do not receive new embeddings or new citation identities.

BM25 groups hits by parent using the best section score, so one large document cannot consume all parent slots. Title/heading/date/body weights are 3/2/1/1. `index_metadata=False` indexes section bodies only but retains metadata for reports. Questions become at most 64 unique quoted keyword terms; full questions still reach callbacks and captures. Ties use stable parent and section identities.

Indexes are in memory by default. An optional `path` creates a new mode-0600 SQLite artifact and never overwrites an existing file. Keep text-bearing indexes outside git and close them with a context manager. Build once and reuse it for the question set; rebuild from the frozen corpus in another process. No production wiki index is modified.

## Candidate selection and relevance ranking

- `mode="vector"` selects the top 50 existing document vectors and does not query SQLite.
- `mode="rrf"` combines top-50 vector parents and top-50 BM25 parents with `sum(1 / (60 + rank))`, then takes the top 50.
- Exclusions apply after each top-50 selection, without refill; original ranks remain intact.
- Ranker body snippets are capped at 400 characters per candidate and 20,000 total. Keyword candidates use a bounded matching-section snippet; vector-only candidates use the shared excerpt helper.
- Metadata and source IDs remain separate from that body cap. Large private metadata should be reviewed before a provider request.
- Receipts record source/corpus/vector hashes, SQLite version, tokenizer, weights, mode, candidate IDs/ranks, index-build time, query times, and body counts.

The vector helper remains a reference implementation, not the optimized production wiki index's throughput benchmark. Index-build time is separate from query time. Section keyword indexing reuses **document** vectors; it is not chunk-vector retrieval and performs no re-embedding.

```python
receipt = await rerank(selection, ranker=relevance_ranker,
    settings={"model": approved_ranker, "temperature": 0, "max_tokens": 800, "seed": 0},
    output=private_ranker_receipt, case_id=case_id, label="ranker-control")
if receipt["status"] != "ok":
    # Preserve this error in the experiment denominator; do not silently refill.
    raise RuntimeError("Relevance ranking failed")
packet = expand_parents(question, receipt["selected_ids"], selection,
    documents, embedding_model=embedding_model)
```

Each ranker capture is JSONL: an input receipt is flushed before the callback, followed by a final success/error receipt. A started receipt without a final receipt is incomplete, not a successful outcome. Each receipt retains its case ID and trial label. Its input hash excludes timings and model settings, so identical ranker inputs can be checked across trials. The full selection hash also records the measured capture. Join these receipts to reader captures by case ID and retain the planned case count. A ranker failure is a pipeline error, not an empty-evidence reader abstention; do not drop it from the denominator.

The caller-owned `relevance_ranker(question=..., candidates=..., instruction=..., settings=...)` callback returns only a JSON-compatible list of IDs. Use the supplied fixed relevance instruction verbatim; it is captured and hashed with the inputs. If the callback adds prompt text or routing, record that separately before interpreting comparisons. Unknown, duplicate, malformed, or more than eight IDs fail; no silent deduplication, clipping, or retry occurs. The callback is distinct from the answer callback and receives no gold labels. Configure callback SDK retries and body logging off.

Parent expansion verifies the same question/corpus/model and uses original parent identities. It includes at most eight 2,500-character parent excerpts, subject to the existing shared 20k preparation cap. A section match cannot relabel a partial section as an independent source. Large parent bodies still need bounded excerpts; their omitted context remains a limitation.

## Controlled reader comparison

`run_readers` in `server/paired_retrieval_eval.py` reuses the existing capture implementation. Supply `packet` (singular), not separate retrieval arms, on each case. The full question and vector/model/hash fields stay as documented in the paired guide.

```python
from paired_retrieval_eval import AnswerSettings, run_readers

summary = await run_readers(reader_cases, documents,
    embedding_model=embedding_model,
    control=AnswerSettings(approved_control),
    candidate=AnswerSettings(approved_stronger_reader),
    complete=answer_callback, output=private_reader_capture,
    exclusions=artifact_ids, corpus_version=snapshot_version)
```

Preparation occurs once per case, then both readers receive identical packet bytes and the unchanged production prompt/validation. Model settings are explicit in `settings_by_arm`. The default remains temperature 0, 400 output tokens, seed 0. Experimental output budgets of 1–16,000 are allowed only when explicitly supplied. Keep budgets equal for a model-only comparison; a deliberate budget change is a different experiment.

Provider errors remain errors, with no retry or model fallback. Captures include complete raw answers, actual reported models, usage, settings, packets, hashes, and timings in new mode-0600 files. Reader-model differences are intentional; reported model overrides remain visible. Every case and reader stays in the denominator, including abstentions/errors. No private body is printed by the helpers.

## Temporal evaluation boundary

See [the synthetic temporal specification](temporal-spec.md). It distinguishes valid time from knowledge/system time. Its unsupported cases are **gaps**, not passing API behavior. No temporal mutation or semantic change is implemented here.
