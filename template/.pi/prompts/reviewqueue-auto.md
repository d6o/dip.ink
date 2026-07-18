---
description: Daily CI-mode pass over [[Curator review queue]]. Resolve deterministic entries, leave subjective or secret-handling work for the operator.
---

# Daily review-queue auto-pass

You are running headless in CI. Your job is to reduce noise in `wiki/Curator review queue.md` once per day without defeating the queue's purpose as the operator's human-review backstop.

## Stance

Be conservative. The hourly multi-batch `/processnotes-auto` curator is allowed to process optimistically; this daily pass is only allowed to prune entries that can be resolved from already-available evidence.

Safe to resolve:

- A contradiction where later wiki/source evidence already proves which side is current.
- A stale queue bullet whose affected page already contains the needed clarification.
- A repo/path/status mismatch that can be verified from existing source notes, wiki pages, or non-secret public metadata.
- A wording cleanup where the queue bullet asks whether to migrate framing, and later canonical repo/live evidence already established the new framing.

Do not auto-resolve:

- **Secrets routed to the vault**. Leave these for the operator because they require uploading/rotating a live credential.
- Anything that asks for taste, business strategy, product positioning without clear newer evidence, or preference.
- Anything requiring credential access, private production mutation, force-push, deleting pages, or removing historical context.
- Any contradiction where the evidence is ambiguous or only one side is represented.

## Workflow

1. Read `wiki/Curator review queue.md`.
2. If the `## Queue` section has no active bullets, make no edits and stop.
3. Process at most 10 active bullets in one run.
4. For each bullet, inspect the affected page(s), cited source note (or legacy source stub), and relevant existing wiki pages.
5. If resolved, make the smallest wiki edit that records the conclusion. Prefer a dated clarification/supersede sentence over rewriting history.
6. Delete only the handled bullet. If all bullets under a per-run section are handled, delete that section. If the whole queue becomes empty, leave a one-line empty-state note under `## Queue`.
7. Leave unresolved bullets exactly in place unless a tiny wording clarification helps the operator understand what remains.
8. Update `updated:` frontmatter on pages you materially edit.
9. Run the wiki validation chain:
   - `python3 scripts/wikilint.py`
   - `python3 scripts/wikiindex.py`
   - `python3 scripts/logrotate.py`
   - `python3 scripts/wikidistill.py --if-stale`
10. If you changed any files, append a top-of-log entry to `wiki/log.md`:

```markdown
## [YYYY-MM-DD HH:MM UTC] note | review queue auto pass — N resolved, M left

Resolved: [[Page A]] — short reason; [[Page B]] — short reason.
Left in queue: M entries, if any. Secrets-to-rotate and subjective calls are intentionally left for the operator.

Validation: wikilint clean (0 errors, 0 warnings); index regenerated; logrotate <result>; distill <result>.
```

If you make no edits, append no log entry. No daily heartbeat commits.

## Guardrails

- Never delete wiki history to make a contradiction disappear. Supersede or clarify.
- Never edit `raw/`.
- Never move or ingest `notes/`; that is `/processnotes-auto`'s job.
- Never commit or push yourself. The runner commits and pushes after you exit.
- Do not create a release yourself. The workflow cuts a release only when your commit lands.
