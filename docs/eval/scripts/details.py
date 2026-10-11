"""Print every disagreement between exact and ANN results (UUID prefixes, ranks, scores)."""
import gzip
import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from analyze import rrf, FINAL  # noqa: E402

rows = [json.loads(line) for line in (gzip.open(sys.argv[1], "rt") if sys.argv[1].endswith(".gz") else open(sys.argv[1]))]
rows = [r for r in rows if "error" not in r]
for r in rows:
    ex = r["exact"]
    ex_ids = [u for u, _ in ex]
    for of, a in sorted(r["ann"].items(), key=lambda t: int(t[0])):
        ann_ids = [u for u, _ in a["res"]]
        miss = [(i + 1, u[:8], round(s, 4)) for i, (u, s) in enumerate(ex) if u not in set(ann_ids)]
        extra = [(i + 1, u[:8], round(s, 4)) for i, (u, s) in enumerate(a["res"]) if u not in set(ex_ids)]
        line = []
        if miss or extra:
            line.append(f"cosine miss={miss} extra={extra}")
        if r["kind"] == "edge":
            fe = rrf([r["bm25"], ex_ids])[:FINAL]
            fa = rrf([r["bm25"], ann_ids])[:FINAL]
            if set(fe) != set(fa):
                ties = len(ex) - len({round(s, 7) for _, s in ex})
                line.append(f"final diff exact-only={[u[:8] for u in fe if u not in fa]} "
                            f"ann-only={[u[:8] for u in fa if u not in fe]} "
                            f"cosine order equal={ex_ids == ann_ids} tied scores in exact={ties}")
        if line:
            print(r["kind"], r["uuid"][:8], f"of={of}", " | ".join(line))
