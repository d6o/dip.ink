---
type: index
tags: [curator, review, queue, meta]
created: 2026-01-01
updated: 2026-01-01
index-description: "Auto-curator decisions that need a human pass — substantial rewrites, contradictions, secrets to rotate. Newest-first. Prune as you review."
---

# Curator review queue

The hourly auto-curator processes notes optimistically — it creates pages, edits pages, changes statuses, writes migrations without asking. This page is the human-review backstop.

The curator appends an entry here only when a decision is non-routine:

- **Substantial rewrites** — when it replaced or re-ordered existing prose rather than adding a dated supersede subsection. Verify the rewrite matches what you'd have written.
- **Contradictions to verify** — when a note claimed something that conflicts with existing wiki content in a way that doesn't read as a clean migration / status change. The curator wrote the note's version optimistically; check which side is current.
- **Secrets routed to the vault** — when the literal-prefix scan matched a value in a note. The curator redacted the value in the source note and referenced a vault path in the wiki page. Upload the live value to your secret manager at the named path and rotate the old one.

Routine actions — new pages, status changes, additive edits, migration subsections, source-note moves — do NOT land here. They're the expected output of the curator and the auto-ingest entry in [[log]] covers them.

## Queue

(empty — nothing awaiting review)
