---
description: Lint, regenerate index.md, rotate log entries, and write a weekly distill if stale. Run after every ingest.
---

# /wikilint

Four-step wiki maintenance: schema lint, index regeneration, log rotation, weekly distill (if stale). Auto-invoked from `/processnotes` step 7; can also be run standalone for a health check.

```bash
python3 scripts/wikilint.py             # 1. Schema + convention lint. Fix all errors and warnings.
python3 scripts/wikiindex.py            # 2. Regenerate wiki/index.md from page frontmatter.
python3 scripts/logrotate.py            # 3. Move log entries >14d into wiki/log/YYYY-Www.md.
python3 scripts/wikidistill.py --if-stale  # 4. Write a distill if the latest is >7 days old.
```

Run them in order. Any errors from step 1 must be fixed before continuing. Steps 2-4 are mechanical and idempotent — step 4 is a no-op when the latest distill is fresh.

## What it checks

The linter enforces the rules in AGENTS.md ("Page conventions" and "Lint" sections). Three severity levels:

- **error** (exit code 1) — frontmatter doesn't parse, filename ≠ H1, broken wikilink (target file doesn't exist case-insensitively).
- **warning** (exit code 2 if no errors) — case mismatch in wikilinks, status enum drift, type not in allowed set, tag not lowercase/hyphenated, dates not `YYYY-MM-DD`, sources not a YAML list of strings, decision page missing required fields.
- **info** (still exit 0/2) — page-length cap exceeded for concept (>150 lines) or entity (>250 lines). Suggest split or `length-exempt: true` in frontmatter.

## How to run it

```bash
python3 scripts/wikilint.py            # scan everything under wiki/
python3 scripts/wikilint.py wiki/foo.md  # scan a specific file
python3 scripts/wikilint.py --json     # machine-readable output
```

Exit codes: `0` clean / `1` errors / `2` warnings only.

## scripts/wikiindex.py — regenerate index.md

After lint is clean, regenerate `wiki/index.md` from page frontmatter:

```bash
python3 scripts/wikiindex.py            # write wiki/index.md
python3 scripts/wikiindex.py --check    # exit 1 if would differ
python3 scripts/wikiindex.py --stdout   # print to stdout (preview)
```

The generator reads each page's `category:` and `index-description:` frontmatter, groups entities by category in the order defined in `ENTITY_CATEGORY_ORDER`, alphabetizes concepts/syntheses/decisions, and collapses sources into a monthly summary. **Don't edit `index.md` by hand** — edit the source page's frontmatter and regenerate.

If a new entity category is invented, add it to `ENTITY_CATEGORY_ORDER` in `scripts/wikiindex.py` first; otherwise it sorts to the bottom with a warning.

## scripts/logrotate.py — rotate old log entries

Move entries from `wiki/log.md` older than 14 days into `wiki/log/YYYY-Www.md` archives:

```bash
python3 scripts/logrotate.py             # rotate, default 14-day window
python3 scripts/logrotate.py --days 60   # different window
python3 scripts/logrotate.py --check     # exit 1 if rotation would change anything
python3 scripts/logrotate.py --dry-run   # print plan, don't write
```

Idempotent. Weekly archive files get `# log YYYY-Www` H1 and a pointer back to the live log. Each archive itself stays append-only — rotation only adds, never removes from archives.

## scripts/wikidistill.py --if-stale — auto-distill

The chain ends with a stale-check distill so weekly snapshots happen on the natural rhythm of ingests:

```bash
python3 scripts/wikidistill.py --if-stale            # default 7-day cadence
python3 scripts/wikidistill.py --if-stale --days 30  # monthly cadence
```

Logic: looks at `wiki/distill/*.md`, finds the date encoded in the newest filename (`YYYY-MM-DD.md`), compares against today. If younger than `--days`, prints `skipping` and exits 0. Otherwise generates a fresh `wiki/distill/<today>.md` covering everything since the previous distill (so a 10-day-late distill spans the full 10 days, not just the trailing 7).

Skip means: nothing was written, nothing got committed by the surrounding flow. The `/processnotes` commit-and-push at step 8 only sees a new distill file when one was actually written, which keeps the diff stream from being noisy on every ingest.

For one-off snapshots regardless of staleness, drop `--if-stale` and use `/wikidistill` directly.

## Workflow

1. Run `python3 scripts/wikilint.py`.
2. **Errors first** (must fix). Common patterns and fixes:
   - **Broken wikilink** `[[Foo]]`: either the page should exist (create it / link to the actual page name) or the link should be removed/converted. Check the surrounding context — `[[Foo]] (folded into [[Bar]])` usually means just link to `Bar` directly.
   - **Filename ≠ H1**: rename the file with `git mv` OR change the H1 to match. Filename wins by Obsidian convention.
   - **Frontmatter parse error**: usually unquoted special characters in `sources:` or stray indentation.
3. **Warnings next** (should fix). Mostly mechanical:
   - **Case mismatch** `[[Docker]] should be [[docker]]`: do a `replace_all` Edit.
   - **Status enum**: pick the closest canonical value. `active` → `live`, `proposal`/`design` → `candidate`, `noted-not-installed` → `not-installed`, `orphan` → `legacy`, etc.
   - **Tag not lowercase / has spaces**: lowercase + hyphenate.
   - **Sources not a list**: rewrite as proper YAML list of quoted wikilinks.
4. **Info notices** (optional, address over time): page-length caps. Either:
   - Split the page into smaller focused concepts/entities. Prefer this for genuinely-multi-topic pages.
   - Set `length-exempt: true` in frontmatter for irreducible kitchen-sink pages. Use sparingly.
5. **Re-run lint** after fixes. Repeat until exit code 0 or 2-with-only-info-notices.

## Don't

- Don't bypass an error by hardcoding `length-exempt: true` to make it green — that field only suppresses page-length info notices.
- Don't fix errors by inventing pages that don't exist. If the link points at a non-existent target, decide: should the page exist (then create it), or should the link be re-pointed (then re-point it).
- Don't suppress warnings the linter raises — they're all addressable. If the linter is wrong, fix the linter (`scripts/wikilint.py`), not the rule.

## Health-check mode (manual)

When the operator asks for a "wiki health check" rather than a /processnotes-step lint, also do the higher-judgment passes the linter can't automate:

- Orphan pages with no inbound wikilinks.
- Missing cross-references in prose (page A mentions an entity name without linking it).
- Stale claims contradicted by newer ingests.
- Important concepts mentioned but lacking their own page.

Produce a short report. Fix what's easy mechanically; propose the rest as TODOs.
