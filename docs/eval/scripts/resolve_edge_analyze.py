"""Compare resolve_edge decisions of candidate models with the batch-router baseline.

Runs locally on the files that resolve_edge_build.py and resolve_edge_run.py made:

  python3 resolve_edge_analyze.py --cases cases.json --calls calls.jsonl \
      --notes note_metrics.json --base base1 --base2 base2 > tables.md

"Effective" decisions apply Graphiti's own index validation
(edge_operations.resolve_extracted_edge): duplicate idx must be in
[0, len(related)); contradicted idx must be in [0, len(related)+len(candidates)).
"""
from __future__ import annotations

import argparse
import json
import random
import statistics as st
from collections import Counter, defaultdict


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def effective(row, case):
    if row is None or row.get("error") or row.get("parse_error"):
        return None
    nrel = len(case["related"])
    ntot = nrel + len(case["candidates"])
    dup = tuple(sorted({i for i in row["dup"] if 0 <= i < nrel}))
    con = tuple(sorted({i for i in row["contra"] if 0 <= i < ntot}))
    bad = [i for i in row["dup"] if not 0 <= i < nrel] + [i for i in row["contra"] if not 0 <= i < ntot]
    return dup, con, bad


def makespan(lat, workers=20):
    """Wall time of n parallel calls with a semaphore of `workers` (greedy)."""
    slots = [0.0] * min(workers, len(lat))
    for x in lat:
        i = slots.index(min(slots))
        slots[i] += x
    return max(slots) if slots else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", action="append", default=[])
    ap.add_argument("--calls", action="append", default=[])
    ap.add_argument("--bundle", default="", help="export file of resolve_edge_export.py")
    ap.add_argument("--groups", default="", help="with --bundle: keep these groups, e.g. CDR or SK")
    ap.add_argument("--notes", default="")
    ap.add_argument("--base", default="base1")
    ap.add_argument("--base2", default="base2")
    ap.add_argument("--disagreements", default="")
    a = ap.parse_args()

    cases = {c["id"]: c for f in a.cases for c in json.load(open(f))["cases"]}
    call_rows = [json.loads(ln) for f in a.calls for ln in open(f)]
    if a.bundle:
        import gzip
        bundle = json.load(gzip.open(a.bundle, "rt"))
        cases.update({c["id"]: c for c in bundle["cases"]})
        call_rows += bundle["calls"]
    if a.groups:
        cases = {k: v for k, v in cases.items() if v["group"] in a.groups}
    rows = {}
    for r in call_rows:
        if r["id"] not in cases:
            continue
        key = (r["config"], r["id"])
        # keep the first successful row; else the last error
        if key in rows and not rows[key].get("error"):
            continue
        rows[key] = r
    configs = sorted({c for c, _ in rows}, key=lambda c: (c not in (a.base, a.base2), c))
    ids = sorted(cases)
    out = []
    p = out.append

    # ---- per-config health, latency, tokens
    p("### Calls, validity, latency, tokens\n")
    p("| config | real model(s) | ok | http err | JSON/schema err | out-of-range idx | p50 s | p90 s | mean s | mean out tok | mean reasoning tok |")
    p("|---|---|---|---|---|---|---|---|---|---|---|")
    lat_by = {}
    for cfg in configs:
        rs = [rows.get((cfg, i)) for i in ids]
        rs = [r for r in rs if r]
        ok = [r for r in rs if not r.get("error") and not r.get("parse_error")]
        herr = sum(1 for r in rs if r.get("error"))
        perr = sum(1 for r in rs if not r.get("error") and r.get("parse_error"))
        bad = sum(1 for r in ok if effective(r, cases[r["id"]])[2])
        lat = [r["latency_s"] for r in ok]
        lat_by[cfg] = lat
        models = Counter(r.get("model") for r in ok)
        out_t = [r.get("completion_tokens") or 0 for r in ok]
        rea_t = [r.get("reasoning_tokens") or 0 for r in ok]
        p(f"| {cfg} | {', '.join(f'{m} ({n})' for m, n in models.most_common())} | {len(ok)} | {herr} | {perr} | {bad} | "
          f"{pct(lat, .5):.2f} | {pct(lat, .9):.2f} | {st.mean(lat):.2f} | {st.mean(out_t):.0f} | {st.mean(rea_t):.0f} |")
    p("")

    def dec(cfg, i):
        return effective(rows.get((cfg, i)), cases[i])

    def kind(d):
        if d is None:
            return None
        return "pos" if (d[0] or d[1]) else "neg"

    # ---- agreement with baseline
    p(f"### Agreement with the baseline ({a.base}), exact set equality of effective decisions\n")
    p("| config | n | both sets equal | duplicate set equal | contradicted set equal | on baseline-positive | on baseline-negative | equal to base1 OR base2 |")
    p("|---|---|---|---|---|---|---|---|")
    disagreements = []
    for cfg in configs:
        if cfg == a.base:
            continue
        n = eq = deq = ceq = 0
        pos = [0, 0]
        neg = [0, 0]
        either = [0, 0]
        for i in ids:
            b = dec(a.base, i)
            c = dec(cfg, i)
            if b is None or c is None:
                continue
            n += 1
            same = b[:2] == c[:2]
            eq += same
            deq += b[0] == c[0]
            ceq += b[1] == c[1]
            bucket = pos if kind(b) == "pos" else neg
            bucket[0] += same
            bucket[1] += 1
            b2 = dec(a.base2, i) if cfg != a.base2 else None
            if b2 is not None:
                either[1] += 1
                either[0] += same or b2[:2] == c[:2]
            if not same:
                disagreements.append({"config": cfg, "id": i, "base": b[:2], "cand": c[:2],
                                      "base2": (dec(a.base2, i) or (None, None))[:2]})

        def fr(x):
            return f"{x[0]}/{x[1]} ({100 * x[0] / x[1]:.1f}%)" if x[1] else "-"
        p(f"| {cfg} | {n} | {eq} ({100 * eq / n:.1f}%) | {deq} ({100 * deq / n:.1f}%) | {ceq} ({100 * ceq / n:.1f}%) | "
          f"{fr(pos)} | {fr(neg)} | {fr(either) if cfg != a.base2 else '-'} |")
    p("")

    # ---- stable baseline: base1 == base2
    stable = [i for i in ids if dec(a.base, i) is not None and dec(a.base, i)[:2] == (dec(a.base2, i) or (None, None))[:2]]
    p(f"### Agreement on cases where the baseline agrees with itself ({a.base} = {a.base2}: {len(stable)} of {len(ids)} cases)\n")
    p("On these cases the baseline answer is stable, so a difference is a model difference, not sampling noise.\n")
    p("| config | both sets equal | missed contradicted idx | extra contradicted idx | missed dup idx | extra dup idx |")
    p("|---|---|---|---|---|---|")
    for cfg in configs:
        if cfg in (a.base, a.base2):
            continue
        n = eq = mc = ec = md = ed = 0
        for i in stable:
            b, c = dec(a.base, i), dec(cfg, i)
            if c is None:
                continue
            n += 1
            eq += b[:2] == c[:2]
            mc += len(set(b[1]) - set(c[1]))
            ec += len(set(c[1]) - set(b[1]))
            md += len(set(b[0]) - set(c[0]))
            ed += len(set(c[0]) - set(b[0]))
        p(f"| {cfg} | {eq}/{n} ({100 * eq / n:.1f}%) | {mc} | {ec} | {md} | {ed} |")
    p("")

    # ---- by group
    p("### Agreement with the baseline by sample group (both sets equal)\n")
    groups = sorted({c["group"] for c in cases.values()})
    p("| config | " + " | ".join(groups) + " |")
    p("|---|" + "---|" * len(groups))
    for cfg in configs:
        if cfg == a.base:
            continue
        cells = []
        for g in groups:
            n = eq = 0
            for i in ids:
                if cases[i]["group"] != g:
                    continue
                b, c = dec(a.base, i), dec(cfg, i)
                if b is None or c is None:
                    continue
                n += 1
                eq += b[:2] == c[:2]
            cells.append(f"{eq}/{n} ({100 * eq / n:.0f}%)" if n else "-")
        p(f"| {cfg} | " + " | ".join(cells) + " |")
    p("")

    # ---- index-level differences: extra vs missed decisions
    p(f"### Index-level differences against {a.base} (sum over all cases)\n")
    p("Extra duplicate = the model merges the new fact into an edge that the baseline keeps apart. "
      "Missed duplicate = the model keeps a new edge that the baseline merges. "
      "Extra contradiction = the model invalidates an edge that the baseline keeps current. "
      "Missed contradiction = the model keeps an edge current that the baseline invalidates.\n")
    p("| config | baseline dup idx | extra dup | missed dup | baseline contra idx | extra contra | missed contra |")
    p("|---|---|---|---|---|---|---|")
    for cfg in configs:
        bd = ed = md = bc = ec = mc = 0
        for i in ids:
            b, c = dec(a.base, i), dec(cfg, i)
            if b is None or c is None:
                continue
            bd += len(b[0])
            bc += len(b[1])
            ed += len(set(c[0]) - set(b[0]))
            md += len(set(b[0]) - set(c[0]))
            ec += len(set(c[1]) - set(b[1]))
            mc += len(set(b[1]) - set(c[1]))
        p(f"| {cfg} | {bd} | {ed} | {md} | {bc} | {ec} | {mc} |")
    p("")

    # ---- decision rates
    p("### Decision rates (share of cases with a non-empty set)\n")
    p("| config | any duplicate | any contradiction | C group: historic invalidated edge in contradicted set |")
    p("|---|---|---|---|")
    for cfg in configs:
        n = d = c_ = 0
        hc = [0, 0]
        for i in ids:
            x = dec(cfg, i)
            if x is None:
                continue
            n += 1
            d += bool(x[0])
            c_ += bool(x[1])
            case = cases[i]
            if case["group"] == "C":
                hist = set(case.get("hist", {}).get("historic_contradicted", []))
                idx = {e["idx"] for e in case["related"] + case["candidates"] if e["uuid"] in hist}
                if idx:
                    hc[1] += 1
                    hc[0] += bool(idx & set(x[1]))
        p(f"| {cfg} | {d}/{n} ({100 * d / n:.0f}%) | {c_}/{n} ({100 * c_ / n:.0f}%) | "
          f"{hc[0]}/{hc[1]} ({100 * hc[0] / max(1, hc[1]):.0f}%) |")
    p("")

    # ---- labeled synthetic groups
    if any(c["group"] in ("S", "K") for c in cases.values()):
        p("### Labeled duplicate test (synthetic new facts from real edges T)\n")
        p("S = meaning-preserving rewrite of T (correct: T in duplicate_facts). "
          "K = T with one key detail changed (correct: T not in duplicate_facts; "
          "T in contradicted_facts is the expected extra answer).\n")
        p("| config | S: T marked duplicate | S: other extra dup idx | K: T not duplicate (correct) | K: T contradicted |")
        p("|---|---|---|---|---|")
        for cfg in configs:
            s_ok = s_n = s_extra = k_ok = k_n = k_con = 0
            for i in ids:
                c = cases[i]
                if c["group"] not in ("S", "K"):
                    continue
                x = dec(cfg, i)
                if x is None:
                    continue
                t = c["hist"]["target_idx"]
                if c["group"] == "S":
                    s_n += 1
                    s_ok += t in x[0]
                    s_extra += bool(set(x[0]) - {t})
                else:
                    k_n += 1
                    k_ok += t not in x[0]
                    k_con += t in x[1]
            p(f"| {cfg} | {s_ok}/{s_n} | {s_extra}/{s_n} | {k_ok}/{k_n} | {k_con}/{k_n} |")
        p("")

    # ---- time per note
    if a.notes:
        notes = json.load(open(a.notes))
        counts = [n["resolve_calls"] for n in notes if n["resolve_calls"]]
        p(f"### Estimated resolve_edge time per note ({len(counts)} notes of the last 3 days, "
          f"mean {st.mean(counts):.1f} calls per note)\n")
        p("Sum = total call seconds per note (what ingest_report counts). Wall = simulated "
          "semaphore_gather with 20 parallel slots, latencies drawn from this test (2,000 draws). "
          "The wall model ignores the extract_timestamps call that follows each new edge and "
          "any slowdown from 20 parallel calls.\n")
        p(f"The baseline row pools the {a.base} and {a.base2} latencies.\n")
        p("| config | mean sum s / note | mean wall s / note | p90 wall s / note | wall saving vs baseline |")
        p("|---|---|---|---|---|")
        rnd = random.Random(7)
        base_wall = None
        for cfg in configs:
            lat = lat_by[cfg] + (lat_by.get(a.base2, []) if cfg == a.base else [])
            if cfg == a.base2:
                continue
            if not lat:
                continue
            sums, walls = [], []
            for _ in range(2000):
                n = rnd.choice(counts)
                draw = [rnd.choice(lat) for _ in range(n)]
                sums.append(sum(draw))
                walls.append(makespan(draw))
            mw = st.mean(walls)
            if cfg == a.base:
                base_wall = mw
            save = f"{base_wall - mw:+.1f} s" if base_wall is not None and cfg != a.base else "-"
            p(f"| {cfg} | {st.mean(sums):.0f} | {mw:.1f} | {pct(walls, .9):.1f} | {save} |")
        p("")

    print("\n".join(out))
    if a.disagreements:
        with open(a.disagreements, "w") as fh:
            for d in disagreements:
                c = cases[d["id"]]
                d["new_fact"] = c["new_fact"]
                d["group"] = c["group"]
                facts = {e["idx"]: e["fact"] for e in c["related"] + c["candidates"]}
                d["n_related"] = len(c["related"])
                involved = set(d["base"][0]) | set(d["base"][1]) | set(d["cand"][0]) | set(d["cand"][1])
                if d["base2"][0] is not None:
                    involved |= set(d["base2"][0]) | set(d["base2"][1])
                d["facts"] = {i: facts.get(i) for i in sorted(involved)}
                fh.write(json.dumps(d) + "\n")


if __name__ == "__main__":
    main()
