---
description: Drain notes/ into the wiki. Dedup via wiki/log.md.
---

# /processnotes

Drain the `notes/` inbox into the wiki. Each note is a folder `YYYY-MM-DD-HHMMSS-<slug>/` containing `<folder>.md` (or legacy `NOTE.md`) and optional attachments, dropped by agent sessions (usually via the `wiki_note_drop` MCP tool).

Full workflow and quality-filter rules are in `AGENTS.md` under "Process notes (`/processnotes`)". Follow that section exactly. Summary:

1. **List `notes/` subdirectories** (skip `README.md` and anything not a folder). If empty, say "inbox empty" and stop.
2. **Dedup against log**: for each note folder name, grep `wiki/log.md` for `ingest` entries mentioning it. Skip any already processed. (Shouldn't happen since folders get deleted after ingest, but guard anyway.)
3. **Secret scan** — read every unprocessed source-note file (`<folder>.md` or legacy `NOTE.md`) and every text attachment (`.log`, `.txt`, `.yaml`, `.yml`, `.env`, `.json`, `.toml`, `.conf`) and scan for:
   - Tokens (strings matching `[A-Za-z0-9_-]{32,}`, `Bearer `, `ghp_`, `sk-`, `xox[abp]-`, `AKIA`, `-----BEGIN`)
   - Passwords (`password:`, `pass:` followed by a non-placeholder value)
   - JWT-like tokens, SSH private keys, API keys

   **Default action when secrets are found: redact-and-reference.** Do not stop; do not put the secret on the wiki. For each secret:

   1. **Pick a secret-manager path** following the convention `/services/<lowercase-service-name>/<SCREAMING_SNAKE_CASE_KEY>` (or your own vault's layout). If the service doesn't exist as a folder yet, propose a new one (lowercase, hyphenated, matching the entity page's name).
   2. **Edit the source-note file in-place** (and any text attachment) to replace the secret value with the literal string `<redacted, see vault at /services/<service>/<KEY>>`. Preserve any non-secret context around it (e.g. "Provider:ApiKey=" prefix, "premium until 2026-05-16" metadata).
   3. After editing, re-grep the note + attachments to confirm the raw secret value no longer appears anywhere.
   4. **Surface to the operator** before continuing: a small table of `note folder | secret kind | proposed vault path | source location (file + how to find the live value)`. Tell them the values still live wherever the note found them, that they need to upload the live values to those vault paths, **and that they should rotate** — the secret was on disk unredacted in note draft form before scrubbing.
   5. Continue with the ingest. The redacted note is what gets summarized and ingested into the wiki; the original-with-secret never reaches a wiki page or a commit (the note folder gets deleted at step 9 anyway).

   **Escape hatch**: if the secret can't be cleanly redacted in-place (binary attachment, deeply nested in something you can't safely edit, or the secret isn't the operator's to store), stop and ask before proceeding.

4. **Read all remaining source-note files**. Group by topic. Related notes ingest together — cross-referencing is the main win of batch processing.
5. **Discuss with the operator — but skip when the proposal is all obvious extensions.** The discuss step is real value when there's a judgment call to make; it's friction otherwise. Skip the dialogue (announce briefly and proceed) when ALL of the following hold:
   - Every note maps cleanly to an existing entity or concept page (no new entity / concept / synthesis / decision pages needed).
   - No note contradicts an existing wiki claim.
   - No note proposes a new entity category, status enum value, or other schema-shaped change.
   - The page list is purely "expand existing X with new section/row" or "move/cite a source note."

   Otherwise — discuss as before: 1–2 sentences per note's takeaway, propose page list (what to create, what to update), ask about emphasis.

   When skipping the dialogue, post a short announcement instead: "N notes; all obvious extensions; touching pages [...] — proceeding." The operator can interrupt if a single line surprises him; otherwise the ingest moves to step 6 immediately. Save the dialogue for the cases where it's load-bearing.
6. **On approval**: write/update wiki pages following the schema (YAML frontmatter, wikilinks, Sources section at the bottom). `sources` in frontmatter must be a **valid YAML list of quoted wikilink strings**:
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
7. **Validate via `/wikilint`** — four steps in order:
   1. `python3 scripts/wikilint.py` — fix every error and warning it surfaces. The linter enforces the schema in `AGENTS.md` (frontmatter rules, status enums, wikilink integrity, filename↔H1, page-length caps). Errors block the ingest. Warnings should be cleared too. Info-level page-length notices can be addressed later or marked with `length-exempt: true` when intentional. Re-run until exit code 0.
   2. `python3 scripts/wikiindex.py` — regenerate `wiki/index.md` from page frontmatter. Don't hand-edit the index; edit `category:` and `index-description:` on the relevant pages, then regenerate.
   3. `python3 scripts/logrotate.py` — rotate log entries older than 14 days into `wiki/log/YYYY-Www.md` archives. No-op when nothing's old enough.
   4. `python3 scripts/wikidistill.py --if-stale` — write a fresh weekly distill to `wiki/distill/<today>.md` if the most recent one is more than 7 days old. No-op otherwise. The auto-distill rides the natural rhythm of `/processnotes` so weekly snapshots happen without remembering to trigger them.

   See `.pi/prompts/wikilint.md` for the full workflow + how to handle each lint category.
8. **Log**: append ONE `ingest` entry to `wiki/log.md` for the whole batch. **Explicitly list every processed note folder name** — this is how dedup works. Format:
   ```
   ## [YYYY-MM-DD] ingest | notes batch (N notes)

   Processed: `YYYY-MM-DD-HHMMSS-slug-1/`, `YYYY-MM-DD-HHMMSS-slug-2/`, ...

   <summary of what was ingested, pages touched, cross-refs noted, attachments copied to assets/>
   ```
9. **Verify the moves landed** — every processed note folder should now exist at `wiki/sources/notes/YYYY/MM/DD/<folder>/` with `<folder>.md`. The `notes/` directory should contain only the inbox README and any unprocessed folders. **Don't `rm -rf`** — the move in step 6 is permanent. If a folder is still in `notes/` after step 6 finished, it wasn't processed and shouldn't be deleted.
10. **Commit**: `ingest: notes batch YYYY-MM-DD`.
11. **Push**: `git push`. Every `/processnotes` run ends with a push so the remote copy stays current — do not skip this step even on a small batch.

## Protocol deviations

If you find a bare `.md` file at the top of `notes/` (not in a folder), the capturing session didn't follow the full protocol. Ingest it anyway if worthwhile, but flag the deviation to the operator so they can decide whether to tighten the directive.

## Quality filter

Not every note becomes wiki content:
- **Thin note** (not enough info to file): ask the operator; if unclear, delete the folder and log as `note | dropped thin: <folder>: <why>`.
- **Stale note** (contradicts newer wiki content): flag the conflict to the operator; usually delete.
- **Duplicate note** (already in wiki): log as `note | dropped duplicate: <folder>`, delete, no pages changed.
- **Wrong note** (claim looks incorrect): flag to the operator. Don't silently correct or silently accept.

Record drops in `log.md` too — auditable trail of what came in and what made it through.

## Don't

- Don't edit `raw/` (notes are not raw sources — they're inbox items).
- Don't commit before the operator approves the page writes.
- Don't process notes without the secret scan. Even one exposed token is a real cost.
