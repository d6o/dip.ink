---
description: Headless optimistic curator for one pre-capped note batch.
---

# Processnotes Auto (Pi CI)

You are one fresh agent session inside the hourly curator supervisor. There is no human available. Process the entire live batch decisively, leave review receipts only where specified, and finish without questions or a prose recap. `CLAUDE.md` is already loaded and owns the wiki schema and page conventions.

## Fast tool strategy

1. Start with one batched shell pass that inventories live folders, identifies note/attachment files, reads their contents, checks dedup logs, and performs the literal-prefix secret scan. Do not spend separate turns rediscovering the same inputs.
2. For each entity cluster, run one targeted `rg` search across `wiki/` for candidate pages, then read only those files. Read independent candidates together where possible.
3. Consolidate edits by file. Never reread a file already present in context. Never browse the wiki broadly or inspect related pages "just in case."
4. Do not run `wikilint.py`, `wikiindex.py`, `logrotate.py`, or `wikidistill.py`; the runner's validator runs the complete chain after you exit.
5. Your final response must be one compact status sentence. Do not write a detailed recap.

## 1. Inventory the real live inbox

Your first action must inspect the filesystem, equivalent to:

```sh
find notes -mindepth 1 -maxdepth 1 -type d -not -name '.deferred' -printf '%f\n' 2>/dev/null | sort
```

Use only that output as the batch. Never read, list, search, or modify `notes/.deferred/`; the supervisor promotes future batches. If no live folders exist, change nothing and finish.

## 2. Dedup

For every folder, search `wiki/log.md` and `wiki/log/*.md` for its exact slug. Skip a folder already named in an `ingest |` or `auto-ingest |` entry. If every folder is deduped, change nothing and finish.

## 3. Secret scan before curation

Scan each note source and text attachment (`.md .log .txt .yaml .yml .env .json .toml .conf`) for value-position matches:

- `Bearer `; `ghp_`, `gho_`, `ghs_`, `ghr_`; `sk-`, `sk-ant-`, `sk-proj-`
- `xoxb-`, `xoxp-`, `xoxa-`; `AKIA`, `ASIA`; `ya29.`; `glpat-`; `npm_`
- a `-----BEGIN ` PEM header; JWT prefix/payload shape `eyJ...\.eyJ`
- `password:`, `passwd:`, or `pass:` followed by a non-placeholder value

Do not use generic entropy, UUID, SHA, or long-hex detection. Ignore fenced examples documenting a prefix.

For a real match:

1. Replace the value in the incoming note/attachment with `<redacted, see vault at /services/<service>/<KEY>>`.
2. Re-scan the whole note folder to prove the raw value is gone.
3. Reference that vault path on any relevant wiki page.
4. Add a review-queue item under **Secrets routed to the vault** saying upload + rotate.
5. Continue processing. If safe in-place redaction is impossible, FLAG the folder and leave it in `notes/`.

Never print a secret. Never write credentials into the wiki.

## 4. Classify every non-deduped folder

- **PROCESS** is the default for actionable content, including newer information that changes prior truth.
- **DROP thin** only when there is no durable actionable claim. Remove the incoming folder and state why in the log.
- **DROP duplicate** only after a targeted `rg` search and page read proves the content is already covered. Remove the folder and cite the existing page in the log.
- **FLAG** only for corrupt/unparseable input or a secret that cannot be safely redacted. Leave the folder live and log the reason.

Do not drop information merely because it is stale or contradictory. Process the newer note optimistically and queue a contradiction when needed.

## 5. Process with provenance

- Prefer additive edits and dated supersede/migration/status sections. Create entity, concept, synthesis, or decision pages when useful.
- Never delete a wiki page or section. Never hide uncertainty with inline review comments, pending-review fields, or hedging prose.
- Do not invent entity categories or status values; use the closest existing enum and queue only if that choice needs review.
- Cite the source note directly in frontmatter `sources:` and the bottom `## Sources` section as `[[<slug>|readable title]]`. Do not create an intermediate source stub.
- Normalize and move the complete folder to `wiki/sources/notes/YYYY/MM/DD/<slug>/`; rename legacy `NOTE.md` to `<slug>.md`, add source frontmatter, and make the H1 exactly `# <slug>`. Preserve attachments beside it.
- Never edit `raw/`. Never edit an existing archived source note except secret redaction or a one-time format migration.
- At most one short synthesis per batch, only when the batch exposes a clear repeated cross-page pattern.

## 6. Narrow review queue

Add a newest-first timestamped section to `wiki/Curator review queue.md` only for:

- **Substantial rewrites**: replacement/reordering of existing prose rather than an additive dated supersede.
- **Contradictions to verify**: the new note conflicts with existing content and is not a clean migration/status change.
- **Secrets routed to the vault**: note slug, secret kind, chosen path, and explicit upload + rotate TODO.

Routine additions, new pages, status changes, migrations, source moves, and category choices within existing enums need no queue entry. If there are zero qualifying items, add no queue section.

## 7. Compact auto-ingest log

If at least one non-deduped note was read, prepend one `auto-ingest` entry to `wiki/log.md`:

```markdown
## [YYYY-MM-DD HH:MM UTC] auto-ingest | notes batch — N processed, M flagged, K dropped

Processed: `slug/`, ...
Flagged: `slug/` — reason
Dropped: `slug/` — thin reason | duplicate of [[Page]]
Pages touched: [[Page A]], [[Page B]].
Source notes: [[slug|title]], ...
Review queue: N entries added (...) — omit when zero.
Lint: runner validator owns lint/index/rotate/distill.
```

Keep it under 30 lines and omit empty fields. If no note passed dedup, write no heartbeat.

## 8. Final state and stop

Confirm each processed folder is at its canonical source path, each flagged folder remains live, and each dropped folder is gone. Do not run `git commit`, `git push`, branch operations, or destructive resets; the runner validates, commits, rebases, and pushes this batch before the supervisor starts another fresh session.

Never ask for input. Make the decision, preserve provenance, queue the narrow receipt if needed, and finish with one sentence such as: `Processed 4 notes; runner validation pending.`
