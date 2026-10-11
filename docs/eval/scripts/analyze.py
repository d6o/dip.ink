"""Summarize results.jsonl from recall_eval.py. Prints Markdown tables."""
import gzip
import json
import statistics
import sys
from collections import defaultdict

FINAL = 10
HIGH = 0.9  # Neo4j cosine score (0-1 range); 0.9 == raw cosine 0.8


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def rrf(lists):
    scores = defaultdict(float)
    for lst in lists:
        for i, u in enumerate(lst):
            scores[u] += 1 / (i + 1)
    items = list(scores.items())
    items.sort(reverse=True, key=lambda t: t[1])
    return [u for u, _ in items]


def recall(exact, ann):
    """Set recall and tie-aware recall. exact/ann: lists of [uuid, score]."""
    if not exact:
        return 1.0, 1.0, 0
    e = {u for u, _ in exact}
    a = {u for u, _ in ann}
    miss = [(u, s) for u, s in exact if u not in a]
    floor = min((s for _, s in ann), default=0.0)
    real = [m for m in miss if m[1] > floor + 1e-9]  # strictly better than the worst returned item
    return (len(e) - len(miss)) / len(e), (len(e) - len(real)) / len(e), len(miss)


def main(path):
    rows = [json.loads(line) for line in (gzip.open(path, "rt") if path.endswith(".gz") else open(path))]
    errs = sum(1 for r in rows if "error" in r)
    rows = [r for r in rows if "error" not in r]
    out = []
    for kind, k in (("node", 15), ("edge", 20)):
        rs = [r for r in rows if r["kind"] == kind]
        out.append(f"\n### {kind} cosine branch (n={len(rs)}, k={k})\n")
        ex_lat = [r["exact_s"] for r in rs]
        sizes = [len(r["exact"]) for r in rs]
        out.append(f"exact result size: mean {statistics.mean(sizes):.1f}, "
                   f"queries with < k results: {sum(1 for s in sizes if s < k)}\n")
        out.append("| overfetch | candidates | ANN-only recall@k mean | min | share recall=1.0 (set) | "
                   "share recall=1.0 (tie-aware) | top-1 found | high-score recall (>=0.9) | underfilled -> exact | "
                   "adapter recall@k mean | adapter share=1.0 | ANN p50 s | ANN p90 s | adapter p50 s | adapter p90 s |")
        out.append("|" + "---|" * 15)
        for of in sorted({o for r in rs for o in r["ann"]}, key=int):
            rec, rec_t, top1, hi_n, hi_f, under, eff, lat, eff_lat = [], [], 0, 0, 0, 0, [], [], []
            worst = []
            for r in rs:
                a = r["ann"][of]
                s, t, nmiss = recall(r["exact"], a["res"])
                rec.append(s)
                rec_t.append(t)
                if not r["exact"] or r["exact"][0][0] in {u for u, _ in a["res"]} or \
                        (a["res"] and abs(a["res"][0][1] - r["exact"][0][1]) < 1e-9):
                    top1 += 1
                hi = [u for u, sc in r["exact"] if sc >= HIGH]
                hi_n += len(hi)
                hi_f += sum(1 for u in hi if u in {x for x, _ in a["res"]})
                lat.append(a["s"])
                if a["underfilled"]:
                    under += 1
                    eff.append(1.0)
                    eff_lat.append(a["s"] + r["exact_s"])
                else:
                    eff.append(t)
                    eff_lat.append(a["s"])
                worst.append((t, s, r["uuid"], nmiss))
            n = len(rs)
            out.append(f"| {of} | {a['candidates']} | {statistics.mean(rec):.4f} | {min(rec):.2f} | "
                       f"{sum(1 for x in rec if x == 1.0) / n:.1%} | {sum(1 for x in rec_t if x == 1.0) / n:.1%} | "
                       f"{top1 / n:.1%} | {hi_f}/{hi_n} | {under} ({under / n:.0%}) | {statistics.mean(eff):.4f} | "
                       f"{sum(1 for x in eff if x == 1.0) / n:.1%} | {pct(lat, 50):.3f} | {pct(lat, 90):.3f} | "
                       f"{pct(eff_lat, 50):.3f} | {pct(eff_lat, 90):.3f} |")
            worst.sort()
            out.append(f"<!-- worst {kind} of={of}: " + "; ".join(
                f"{u[:8]} tie-aware={t:.2f} set={s:.2f} miss={m}" for t, s, u, m in worst[:5]) + " -->")
        out.append(f"\nexact latency: p50 {pct(ex_lat, 50):.2f} s, p90 {pct(ex_lat, 90):.2f} s, "
                   f"mean {statistics.mean(ex_lat):.2f} s, max {max(ex_lat):.2f} s\n")
        if kind != "edge":
            continue
        # Hybrid RRF: bm25 list first, then cosine list (search_tasks order in edge_search).
        out.append("\n### edge hybrid RRF (invalidation-candidate search, final top 10)\n")
        out.append("| overfetch | cosine misses (all ranks) | misses also in BM25 top 20 | misses in exact final top 10 | "
                   "final top-10 recall mean | share final recall=1.0 | min |")
        out.append("|---|---|---|---|---|---|---|")
        for of in sorted({o for r in rs for o in r["ann"]}, key=int):
            misses = rescued = in_final = 0
            frec = []
            for r in rs:
                a = r["ann"][of]
                ann_list = [u for u, _ in a["res"]] if not a["underfilled"] else [u for u, _ in r["exact"]]
                ex_list = [u for u, _ in r["exact"]]
                fe = rrf([r["bm25"], ex_list])[:FINAL]
                fa = rrf([r["bm25"], ann_list])[:FINAL]
                m = [u for u in ex_list if u not in set(ann_list)]
                misses += len(m)
                rescued += sum(1 for u in m if u in set(r["bm25"]))
                in_final += sum(1 for u in m if u in set(fe))
                frec.append(len(set(fe) & set(fa)) / len(fe) if fe else 1.0)
            n = len(rs)
            out.append(f"| {of} | {misses} | {rescued} | {in_final} | {statistics.mean(frec):.4f} | "
                       f"{sum(1 for x in frec if x == 1.0) / n:.1%} | {min(frec):.2f} |")
        bm = [r["bm25_s"] for r in rs]
        out.append(f"\nBM25 latency: p50 {pct(bm, 50):.3f} s, p90 {pct(bm, 90):.3f} s; "
                   f"empty BM25 lists: {sum(1 for r in rs if not r['bm25'])}\n")
        rel = [r["related"] for r in rs]
        nb = [x["n_between"] for x in rel]
        out.append("\n### related-edges search (SearchFilters(edge_uuids=...), exact path)\n")
        out.append(f"edges between endpoints (excl. self): mean {statistics.mean(nb):.2f}, max {max(nb)}, "
                   f"zero: {sum(1 for x in nb if x == 0)}\n")
        for key in ("between_s", "cosine_s", "bm25_s"):
            xs = [x[key] for x in rel]
            out.append(f"- {key}: p50 {pct(xs, 50) * 1000:.1f} ms, p90 {pct(xs, 90) * 1000:.1f} ms, "
                       f"max {max(xs) * 1000:.1f} ms")
    print("\n".join(out))
    print(f"\nrows {len(rows)}, error rows skipped {errs}")


if __name__ == "__main__":
    main(sys.argv[1])
