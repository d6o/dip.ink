---
description: Weekly wiki synthesis pass. Looks for repeated source-note patterns that deserve a synthesis page, writes up to 3 non-duplicative syntheses, and logs the pass.
---

# /synthesis-auto

Headless weekly pass for Wikipedia-style consolidation. You are running in CI. Do not ask questions.

## Goal

Find places where the wiki has accumulated many source notes and entity/concept edits, but lacks a short synthesis page that explains the cross-cutting pattern. Synthesis pages should reduce future re-derivation; they must not duplicate raw note narratives.

## Rules

- Create or update **at most 3** synthesis pages per run.
- Prefer one strong synthesis over three weak ones.
- A synthesis is warranted when at least 3 source notes or 3 pages point at the same workflow / failure mode / migration / operating pattern and no existing synthesis already explains it.
- Link heavily: entity/concept pages and source notes are the evidence. Do not reprint long note details.
- Do not create source stubs. Source notes live at `wiki/sources/notes/YYYY/MM/DD/<slug>/<slug>.md` and are linked as `[[<slug>|short title]]`.
- Do not edit source notes. They are frozen citations.
- Do not invent new entity categories or status enums.

## Workflow

1. Read `wiki/index.md`, the latest `wiki/distill/*.md`, and the last ~2 weeks of `wiki/log.md` / `wiki/log/*.md`.
2. Identify candidate clusters. Good signals:
   - same entity appears in many recent source notes;
   - repeated phrases like "pattern", "gotcha", "runbook", "migration", "failure mode", "cron", "portfolio";
   - long entity pages with several dated subsections that would benefit from an overview.
3. For each selected candidate, search for an existing synthesis. Update it if it exists; otherwise create a new `type: synthesis` page.
4. Keep pages focused. A good synthesis usually has:
   - frontmatter (`type: synthesis`, tags, created/updated, sources);
   - H1 matching filename;
   - 1 paragraph thesis;
   - 3-7 bullets or short sections explaining the pattern;
   - `## Sources` listing the linked source notes and high-level pages.
5. Run:
   - `python3 scripts/wikilint.py`
   - `python3 scripts/wikiindex.py`
   - `python3 scripts/logrotate.py`
   - `python3 scripts/wikidistill.py --if-stale`
6. If anything changed, append a compact `note` entry near the top of `wiki/log.md`:

```markdown
## [YYYY-MM-DD HH:MM UTC] note | synthesis auto pass

Created/updated syntheses: [[Page A]], [[Page B]].
Evidence clusters: [[Entity A]], [[Concept B]], [[Source Note Slug|short source title]].
Lint: clean; index/logrotate/distill run.
```

If no synthesis was warranted, leave the repo unchanged.

## Stop

Do not commit or push. The runner handles commit + push if there is a diff.
