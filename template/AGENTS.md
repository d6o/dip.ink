# wiki — an LLM-maintained personal knowledge base

This repo is the operator's externalized memory. It's a plain markdown wiki that LLM agents maintain on the operator's behalf: instead of re-deriving knowledge from raw sources on every query (RAG-style), the LLM compiles knowledge once into a persistent, interlinked wiki and keeps it current as new sources arrive.

**You are the wiki maintainer.** The operator curates sources and asks questions; you do the reading, summarizing, cross-referencing, filing, and bookkeeping.

## Layout

```
raw/         # Immutable source documents. Read these; never modify.
notes/       # Transient inbox. One folder per note dropped by an agent
             # session (usually via the wiki_note_drop MCP tool), each
             # containing <folder>.md (or legacy NOTE.md) + optional
             # attachments. The hourly curator drains this into
             # wiki/sources/notes/YYYY/MM/DD/<folder>/.
  .deferred/ # Supervisor-held folders outside the current oldest-first batch.
  .blocked/  # Terminal quarantine for FLAGged, malformed, or already-ingested folders.
             # Each complete original folder gets a machine-readable BLOCKED.md.
wiki/        # Your output. Markdown pages — entities, concepts, summaries, syntheses.
  index.md   # Catalog of every wiki page. Auto-generated; don't edit by hand.
  log.md     # Append-only chronological record of ingests, queries, lint passes.
             # Entries >14d rotate to wiki/log/YYYY-Www.md archives.
  Curator review queue.md
             # Hand-pruned queue of auto-curator decisions that need the
             # operator's eyes (substantial rewrites and contradictions).
             # Newest-first; the operator deletes entries as they review.
  assets/    # Images downloaded from sources.
  sources/   # Source pages.
    notes/YYYY/MM/DD/<YYYY-MM-DD-HHMMSS-slug>/
             # Permanent source-note folder. The canonical source page is
             # <YYYY-MM-DD-HHMMSS-slug>.md; attachments live beside it.
AGENTS.md    # This file. The schema. Co-evolves with the wiki.
```

This repo is **data-only**. The curator toolchain (wikilint.py, wikiindex.py,
logrotate.py, wikidistill.py, the processnotes-* scripts, and the headless
curator prompts) ships inside the `pi-runner` image at `/opt/dip.ink/scripts/`
and `/opt/dip.ink/prompts/`, versioned and released from the dip.ink repo.
Workflow files under `.github/workflows/` pin an immutable pi-runner tag and
are pure configuration. An optional `wiki/.wikiindex.yml` can override the
entity-category ordering used by the index generator (`entity-category-order:`).

## Link style

Use Obsidian wikilinks everywhere: `[[Page Name]]` or `[[Page Name|display text]]`. No `.md` extension, no directory prefix. This is what makes the Obsidian graph view and backlinks work. Only use standard `[text](url)` links for **external** URLs (web sources, references).

**Critical: filename must equal the H1 heading exactly.** Obsidian resolves `[[Foo]]` by matching against the **filename** (minus `.md`), not against the H1. A file named `foo.md` with heading `# Foo Bar Baz` is *not* reachable via `[[Foo Bar Baz]]` — clicking that wikilink will create a new empty file instead of opening the existing one. Every page must follow:
- Filename: `<Title>.md` (with spaces, parentheses, dates — whatever is in the title).
- First non-frontmatter line: `# <Title>` matching the filename exactly.
- Wikilinks to that page: `[[<Title>]]`.

If a page's title changes, rename the file with `git mv` at the same time, and update every wikilink that points to it.

## Page conventions

Every wiki page starts with YAML frontmatter:

```yaml
---
type: entity | concept | source | synthesis | decision | log | index
category: "Services"   # entity pages only — drives index.md grouping
tags: [lowercase-hyphenated, tag, list]
created: 2026-04-22
updated: 2026-04-22
status: live   # entity + decision pages only — see enums below
sources:
  - "[[Source Page A]]"
  - "[[Source Page B]]"   # only for non-source pages
index-description: "One-line description shown in index.md. Optional override; falls back to first paragraph."
length-exempt: true   # optional escape hatch when page is intentionally over the cap
---
```

### Frontmatter rules (enforced by `wikilint.py`)

- **`type`** ∈ `{entity, concept, source, synthesis, decision, log, index}`. No other values.
- **`tags`** must be a YAML list of strings, each **lowercase + hyphenated** (no spaces, no camelcase).
- **`created` / `updated`** must be `YYYY-MM-DD` (no times, no timezones).
- **`sources`** must be a YAML list of quoted wikilink strings. Never shorthand like `sources: [[Foo]], [[Bar]]`. For non-source pages, the frontmatter `sources` list and the bottom-of-page **Sources** section should contain the same pages. Source pages themselves do **not** get a `sources:` field.
- **`status`** is required on **entity** and **decision** pages. Other page types should not carry one (the linter flags it as info if they do).
- **`category`** is required on **entity** pages — it drives the section grouping in `index.md`. Pick from the wiki's category list (`wiki/.wikiindex.yml` `entity-category-order:`, or the defaults in `wikiindex.py`); add a new one only if no existing category fits, and update the order list at the same time.
- **`index-description`** is optional but encouraged. The index generator falls back to the page's first paragraph if missing — fine for most pages, but explicit one-liners read better. Keep ≤220 chars.

### Status enums

Entity status: `live`, `degraded`, `dormant`, `retired`, `broken`, `candidate`, `not-installed`, `legacy`. Pick the closest match — when in doubt, `live` for production, `candidate` for designs not yet built, `legacy` for orphaned-but-still-running.

Decision status: `undecided`, `decided`, `superseded`. When `superseded`, also include `superseded-by: "[[Other Decision Page]]"`.

### Page types

- **entity** — a thing with identity: a tool, service, person, product, org, place. Has `status:`.
- **concept** — an idea, pattern, technique, principle.
- **source** — a single ingested raw document or note. Source notes live at `wiki/sources/notes/YYYY/MM/DD/<slug>/<slug>.md`, with attachments beside the page. The source note is the citation; durable knowledge lives in entity/concept/synthesis/decision pages.
- **synthesis** — a page combining multiple sources/entities/concepts into something new: a comparison, a writeup, an analysis.
- **decision** — an explicit decision record (`status: undecided | decided | superseded`).
- **log** — reserved for `log.md`.
- **index** — meta-page type. Covers `index.md` (auto-generated catalog) and `Curator review queue.md` (hand-pruned auto-curator review queue). Index-type pages are skipped by `wikiindex.py`.

### Page length

Keep pages short and focused. The linter's caps:

- **Concept**: ≤150 lines
- **Entity**: ≤250 lines
- **Source / synthesis / decision**: no cap. Source notes may be long because they preserve raw capture, but do not duplicate that raw narrative into entity/concept pages; link instead.

Over the cap, either split into smaller pages or set `length-exempt: true` in frontmatter when the size is intentional. Use the escape hatch sparingly. If a page is just over the cap, prefer trimming over exempting.

One page per distinct thing. If a page starts covering two things, split it. Prefer many small linked pages over few big ones — that's what makes the graph useful.

### Sources section

At the bottom of every non-source page, include a **Sources** section listing the source pages that informed it. Link directly to the source-note page with a readable alias, e.g. `[[2026-05-16-200207-device-auth-fix|device auth note]]`. When claims conflict between sources, call it out inline ("Source A says X; Source B says Y — note contradiction").

## index.md (auto-generated)

`wiki/index.md` is regenerated from page frontmatter by `wikiindex.py`. **Don't hand-edit it.** Instead, change the source page's `category:` (entities only) or `index-description:` and re-run the generator. The generator runs automatically in the validator chain after every curator batch.

The Sources section of the index is intentionally a count-by-month, not a per-page list. Browse `wiki/sources/notes/` directly (date-partitioned folders) or grep `wiki/log.md` for ingest narratives.

## log.md format

Append-only. Every entry starts with `## [YYYY-MM-DD] <action> | <short title>` or `## [YYYY-MM-DD HH:MM UTC] <action> | <short title>` so it's greppable. Actions: `ingest`, `auto-ingest`, `query`, `lint`, `note`. Body is compact revision history: notes processed, pages touched, source notes linked, any follow-ups. Do not re-narrate the source note or entity-page prose in the log.

The live `wiki/log.md` keeps roughly the last 14 days. Older entries rotate into `wiki/log/YYYY-Www.md` weekly archives via `logrotate.py` (run automatically in the validator chain). Archives are themselves append-only — rotation only adds, never reorders or removes.

## Operations

### Ingest
1. The operator drops a source into `raw/` (or points you at one) and asks you to process it.
2. Read the source.
3. **Discuss key takeaways with the operator first** before writing. One or two sentences per takeaway. Ask if emphasis is right.
4. Write a source page under the source-note shape when applicable (`wiki/sources/notes/YYYY/MM/DD/<slug>/<slug>.md`).
5. Update or create entity/concept pages touched by the source. A single source commonly touches 5–15 wiki pages; don't be shy about fanning out.
6. Update `wiki/index.md` — regenerate via `wikiindex.py`.
7. Append an `ingest` entry to `log.md`.

### Query
1. Read `wiki/index.md` first to find relevant pages.
2. Drill into those pages; follow wikilinks as needed.
3. Synthesize an answer with wikilinks to the pages you drew from.
4. **If the answer is non-trivial, offer to file it back as a synthesis page.** Comparisons, analyses, and new connections are valuable — don't let them die in chat.
5. Append a `query` entry to `log.md`.

### Process notes (auto-curate)

Agent sessions drop notes into `notes/` when they learn things (via the `wiki_note_drop` MCP tool — see `AGENTS.md` in the dip.ink repo). Each note is a **folder** named `YYYY-MM-DD-HHMMSS-<slug>/` containing `<folder>.md` (or legacy `NOTE.md`) plus any attachments. The hourly headless curator drains this inbox.

`notes/.blocked/` is terminal and excluded from both live and deferred batching. FLAGged folders, malformed frontmatter, and exact already-ingested duplicates move there intact; no existing source-note or attachment bytes are changed. `processnotes-block-note.sh` adds only a bounded `BLOCKED.md` receipt with `slug`, safe `reason`, and `blocked-at` frontmatter.

**Credentials, tokens, and passwords must never be submitted in notes.** Agents record only the corresponding secret-manager path.

The hourly scheduled supervisor (`/opt/dip.ink/scripts/processnotes-supervisor.sh` in the pi-runner image) repeatedly exposes the oldest N=4 notes and launches an independent fresh agent session from `/opt/dip.ink/prompts/processnotes-auto.md`. Each successful sub-batch validates, commits, rebases, and pushes before another begins; it stops on no HEAD advance, an empty inbox, or when its time budget runs low.

**Stance: optimistic with receipts.** The auto-curator processes notes aggressively without asking — creates new pages, edits existing ones, changes statuses, writes migrations. Every change is reversible via git; the operator is the only consumer; the cost of unprocessed notes piling up is worse than the cost of an imperfect edit fixed later.

Curator conventions:

- **No human-in-the-loop discussion.** Decisions are made and committed.
- **New pages OK.** New entity / concept / synthesis / decision pages can be created freely. New entity `category:` values and new status enum values still require manual judgment.
- **Prefer superseding over rewriting.** Status changes and migrations always add a dated subsection; the prior prose stays. **Never delete a page or section.** A retired service stays with `status: retired` and a migration subsection; an obsolete claim gets a dated supersede note, not a removal.
- **Narrow review queue.** Most notes get processed. Decisions that genuinely need the operator's judgment land as one-line bullets in `wiki/Curator review queue.md` under two buckets: **Substantial rewrites** and **Contradictions to verify**. Routine actions do NOT go on the queue.
- **Blocked means terminal.** Corrupt/unparseable folders and exact already-ingested duplicates move intact to `notes/.blocked/`; they never remain live or return through `.deferred/`.
- **Pages stay clean.** No inline `<!-- needs review -->` markers, no hedging prose. The queue is the only place that says "look at this."
- **`auto-ingest` log entries.** Each sub-batch that read at least one note appends one compact entry to `wiki/log.md`.
- **Synthesis pressure.** Each sub-batch does a one-synthesis-max check for repeated patterns. A separate weekly synthesis pass (`/opt/dip.ink/prompts/synthesis-auto.md`) can create/update up to three synthesis pages when patterns accumulate.

### Lint + index + rotate + distill (validator chain)

The validator chain runs four scripts: `wikilint.py` (schema), `wikiindex.py` (regenerate index.md), `logrotate.py` (rotate old log entries), and `wikidistill.py --if-stale` (weekly distill if stale). The runner executes it automatically after every curator batch; it can also be invoked standalone from `/opt/dip.ink/scripts/` (or a dip.ink checkout).

Linter output severity:

- **error** — must fix before commit (frontmatter parse fail, filename ≠ H1, broken wikilink).
- **warning** — should fix (case mismatch in wikilinks, status enum drift, malformed sources/dates/type/tags).
- **info** — consider (page-length cap exceeded; address via split or `length-exempt: true`).

Exit codes: `0` clean / `1` errors / `2` warnings only.

## Working rules

- **Never modify `raw/`.** It's the source of truth. Read-only.
- **Never re-batch `notes/.blocked/`.** Preserve every pre-existing file byte in a blocked folder; only the standard receipt may be added.
- **Never capture credentials, tokens, or passwords.** Reference their secret-manager paths instead.
- **Prefer small edits to many pages** over one big rewrite. Spread the knowledge; keep pages focused.
- **Always update `index.md` and `log.md` on every ingest.** Non-negotiable — they're what makes the wiki navigable.
- **Wikilinks, not filesystem paths.** `[[Foo]]` not `wiki/foo.md`.
- **Commit after each ingest or significant update.** Short message: `ingest: <source title>` or `update: <what changed>`.
- **Push after every curation pass.** The remote copy is the canonical backup, not an optional mirror.
- **Ask before inventing categories.** If a new top-level category in `index.md` seems warranted, propose it first — tag sprawl is the failure mode.

## Scope

The wiki covers whatever the operator wants their future agent sessions to know:
- Tools they have and how they use them.
- How they deploy things; infra conventions.
- Workflows, decisions, and their reasoning.
- Learnings distilled from articles, papers, and podcasts they feed in.
- Project-specific context (though each project's own AGENTS.md still owns its code-facing rules).

When in doubt about whether something belongs, ask.
