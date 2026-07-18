---
description: Drain notes/ into the wiki. Dedup via wiki/log.md.
---

# /processnotes

Drain the `notes/` inbox into the wiki. Each note is a folder `YYYY-MM-DD-HHMMSS-<slug>/` containing `<folder>.md` (or legacy `NOTE.md`) and optional attachments, dropped by agent sessions (usually via the `wiki_note_drop` MCP tool).

Full workflow and quality-filter rules are in `AGENTS.md` under "Process notes (`/processnotes`)". Follow that section exactly. Summary:

**Credentials, tokens, and passwords must never be submitted in notes or written to the wiki. Reference only the corresponding secret-manager path.**

1. **List live `notes/` subdirectories** (skip `README.md`, `notes/.deferred/`, `notes/.blocked/`, and anything not a folder). Existing blocked entries are terminal: never include them in a normal batch. If no live folders exist, say "inbox empty" and stop.
2. **Dedup against log**: for each live folder, run `python3 scripts/processnotes-is-ingested.py <slug>`. For an exact prior `ingest |` or `auto-ingest |` match, run `bash scripts/processnotes-block-note.sh <slug> already-ingested`. This moves the complete folder to `notes/.blocked/<slug>/`, preserves every existing note/attachment byte, and adds a machine-readable `BLOCKED.md` receipt. Never skip an exact duplicate in place. If every live folder is terminally deduped, commit and push those blocked moves without writing a wiki/log heartbeat.
3. **Read all remaining source-note files**. Group by topic. Related notes ingest together — cross-referencing is the main win of batch processing.
4. **Discuss with the operator — but skip when the proposal is all obvious extensions.** The discuss step is real value when there's a judgment call to make; it's friction otherwise. Skip the dialogue (announce briefly and proceed) when ALL of the following hold:
   - Every note maps cleanly to an existing entity or concept page (no new entity / concept / synthesis / decision pages needed).
   - No note contradicts an existing wiki claim.
   - No note proposes a new entity category, status enum value, or other schema-shaped change.
   - The page list is purely "expand existing X with new section/row" or "move/cite a source note."

   Otherwise — discuss as before: 1–2 sentences per note's takeaway, propose page list (what to create, what to update), ask about emphasis.

   When skipping the dialogue, post a short announcement instead: "N notes; all obvious extensions; touching pages [...] — proceeding." The operator can interrupt if a single line surprises him; otherwise the ingest moves to step 5 immediately. Save the dialogue for the cases where it's load-bearing.
5. **On approval**: write/update wiki pages following the schema (YAML frontmatter, wikilinks, Sources section at the bottom). `sources` in frontmatter must be a **valid YAML list of quoted wikilink strings**:
   ```yaml
   sources:
     - "[[Source Page A]]"
     - "[[Source Page B]]"
   ```
   Never use shorthand like `sources: [[Foo]], [[Bar]]`.

   **Source notes are the source pages.** For each ingested note:
   - Normalize the note file to wiki-compatible source frontmatter (`type: source`, `tags`, `created`, `updated`, `captured`, `session`, `topic`, `index-description`) and make the H1 exactly `# <folder>`.
   - Move the note folder to `wiki/sources/notes/YYYY/MM/DD/<YYYY-MM-DD-HHMMSS-slug>/` and name the source file `<YYYY-MM-DD-HHMMSS-slug>.md`. **Use plain `mv`, not `git mv`** — incoming notes are untracked, and `git mv` errors out with `fatal: source directory is empty` on untracked content.
   - Link that source note from page frontmatter and bottom Sources sections as `[[YYYY-MM-DD-HHMMSS-slug|short readable title]]`.

   Do not create an intermediate `wiki/sources/<Human Title>.md` stub. The source note is the citation; durable prose goes into entity / concept / synthesis pages.

   For attachments worth elevating to wiki-asset status (diagrams referenced by a concept page, hero screenshots), copy into `wiki/assets/` with a descriptive filename and link from the relevant page. Attachments that only document the note itself stay in the note folder.
6. **Validate via `/wikilint`** — four steps in order:
   1. `python3 scripts/wikilint.py` — fix every error and warning it surfaces. The linter enforces the schema in `AGENTS.md` (frontmatter rules, status enums, wikilink integrity, filename↔H1, page-length caps). Errors block the ingest. Warnings should be cleared too. Info-level page-length notices can be addressed later or marked with `length-exempt: true` when intentional. Re-run until exit code 0.
   2. `python3 scripts/wikiindex.py` — regenerate `wiki/index.md` from page frontmatter. Don't hand-edit the index; edit `category:` and `index-description:` on the relevant pages, then regenerate.
   3. `python3 scripts/logrotate.py` — rotate log entries older than 14 days into `wiki/log/YYYY-Www.md` archives. No-op when nothing's old enough.
   4. `python3 scripts/wikidistill.py --if-stale` — write a fresh weekly distill to `wiki/distill/<today>.md` if the most recent one is more than 7 days old. No-op otherwise. The auto-distill rides the natural rhythm of `/processnotes` so weekly snapshots happen without remembering to trigger them.

   See `.pi/prompts/wikilint.md` for the full workflow + how to handle each lint category.
7. **Log**: append ONE `ingest` entry to `wiki/log.md` for the whole batch. **Explicitly list every processed note folder name** — this is how dedup works. Format:
   ```
   ## [YYYY-MM-DD] ingest | notes batch (N notes)

   Processed: `YYYY-MM-DD-HHMMSS-slug-1/`, `YYYY-MM-DD-HHMMSS-slug-2/`, ...

   <summary of what was ingested, pages touched, cross-refs noted, attachments copied to assets/>
   ```
8. **Verify the moves landed** — every processed note folder should now exist at `wiki/sources/notes/YYYY/MM/DD/<folder>/` with `<folder>.md`. The `notes/` directory should contain only the inbox README, `.deferred/`, `.blocked/`, and any intentionally unprocessed live folders. **Don't `rm -rf`** — the move in step 5 is permanent. If a folder is still live in `notes/` after step 5 finished, it wasn't processed and shouldn't be deleted.
9. **Commit**: `ingest: notes batch YYYY-MM-DD`.
10. **Push**: `git push`. Every `/processnotes` run ends with a push so the remote copy stays current — do not skip this step even on a small batch.

## Protocol deviations

If you find a bare `.md` file at the top of `notes/` (not in a folder), the capturing session didn't follow the full protocol. Ingest it anyway if worthwhile, but flag the deviation to the operator so they can decide whether to tighten the directive.

## Quality filter

Not every note becomes wiki content:
- **Thin note** (not enough info to file): ask the operator; if unclear, delete the folder and log as `note | dropped thin: <folder>: <why>`.
- **Stale note** (contradicts newer wiki content): flag the conflict to the operator; usually delete.
- **Duplicate note** (content is already covered but its exact slug was not previously logged): log as `note | dropped duplicate: <folder>`, delete, no pages changed.
- **Wrong or malformed note**: flag to the operator. If it should be quarantined, run `bash scripts/processnotes-block-note.sh <slug> needs-operator-review` (or the narrower `corrupt-input`, `unparseable-input`, or `malformed-note` reason). Do not edit the note or attachments before blocking.

Record drops in `log.md` too — auditable trail of what came in and what made it through.

## Don't

- Don't edit `raw/` (notes are not raw sources — they're inbox items).
- Don't read or re-batch existing entries under `notes/.blocked/`.
- Don't commit before the operator approves the page writes.
