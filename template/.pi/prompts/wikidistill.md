---
description: Emit a weekly (or monthly/quarterly) snapshot of wiki changes to wiki/distill/YYYY-MM-DD.md.
---

# /wikidistill

Run `scripts/wikidistill.py` to produce a "what changed this week" snapshot of the wiki — new pages, modified pages, lint state — written to `wiki/distill/YYYY-MM-DD.md`.

```bash
python3 scripts/wikidistill.py             # last 7 days, default
python3 scripts/wikidistill.py --days 30   # monthly
python3 scripts/wikidistill.py --days 90   # quarterly
python3 scripts/wikidistill.py --stdout    # preview without writing
python3 scripts/wikidistill.py --since 2026-04-22  # explicit start
```

## When to run

- **Weekly** is the recommended default. Pick a day (Monday morning works well) and run it.
- **Monthly** for slower-trend visibility — pages that shifted role, status changes, etc.
- **Quarterly** as a state-of-the-union read.

The three cadences are not exclusive — distills are cheap to generate, and each writes its own dated file. Read them when you need a "what did I miss" view; skip them otherwise.

## What it surfaces

Per the script's output:

1. **At a glance** — change counts (new / modified / deleted pages, asset changes, by type), and the current `wikilint` summary line.
2. **New pages** — grouped by type (entity / concept / synthesis / decision / source), each with its `index-description`.
3. **Modified pages** — same shape, for pages that existed before the window.
4. **Deletions** and **asset changes** if any.

## What it doesn't do

- It doesn't propose changes — it reports state. If a distill surfaces something stale or contradictory, that's a cue to run `/wikilint` (or open a separate session to address it).
- It's not a synthesis page — distills are `type: log` so they don't pollute the auto-generated index.

## Suggested workflow

1. Run `/wikidistill` first thing on the day you've designated.
2. Skim the "At a glance" section.
3. Drill into anything that surprises (new entity? unusual modification volume? lint regressions?).
4. The distill itself stays in `wiki/distill/YYYY-MM-DD.md` — historical browsing later.

For a deeper read at month or quarter boundaries, run with `--days 30` or `--days 90`. Past-period distills can also help reconstruct "what was the wiki like in week X" if you ever need to rewind.
