---
description: Flush wiki-worthy learnings from this session into the operator's memory via wiki_note_drop, then acknowledge compaction for 30 min.
---

You are running the **recordnotes** flush. Review the current session and preserve durable learnings as note(s) in the memory's inbox via the `wiki_note_drop` tool, then write the compact-ack marker so compaction can proceed.

This only writes to the `notes/` inbox for the curator to ingest later. **Never edit the wiki directly.**

## What to record

Capture things a future session would be glad to know without re-deriving: tools/services/deploys/workflows, architecture discoveries and runtime behavior, decisions + their reasoning, debugging findings/failure modes/gotchas, useful commands/paths/URLs/artifact locations. Err toward capture — duplicate notes are cheap, missed captures are expensive.

Skip ephemeral chatter, obvious diffs with no extra insight, and things already in code unless the **why** is non-obvious.

## Workflow

1. Review the current session and identify durable learnings.
2. Group them into **1–3 coherent notes by topic** (one topic per note).
3. If nothing durable was learned, say so — but still write the ack marker below.
4. For each note, call `wiki_note_drop` with a short kebab-case **slug** (no timestamp; the server prepends UTC time) and `note_md` containing this frontmatter:

   ```yaml
   ---
   captured: <ISO 8601 datetime>
   session: <1-line description of what you were doing>
   topic: <1–5 word topic>
   ---
   ```

   Over-explain the body — the curator cannot read this transcript later. Include exact paths/commands/URLs/artifact names. Reference attachments via relative paths. **Never include credentials, tokens, or passwords** — say where they live instead.

5. At the end, **always** write/update the compact-ack marker for the current working directory. This is what lets compaction proceed for the next 30 minutes — it's the contract the `session_before_compact` gate checks:

   ```bash
   key=$(printf %s "$PWD" | shasum -a 256 | cut -d' ' -f1)
   dir="$HOME/.pi/agent/recordnotes-acks"
   mkdir -p "$dir"
   ts=$(date +%s)
   iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
   printf '{"cwd":"%s","ts":%s,"reviewed_at":"%s"}\n' "$PWD" "$ts" "$iso" > "$dir/$key.json"
   echo "ack: $dir/$key.json"
   ```

   Do this even if no note was created — the marker means "this directory was reviewed for capture recently," not "a note was definitely created."

If `wiki_note_drop` is unavailable or fails, report the failure explicitly — a note that didn't reach the server is not recorded.

## Report

When done, report either the `wiki_note_drop` folder/commit/URL values grouped by topic, or that no new note was needed. Then confirm compaction is acknowledged for this working directory for 30 minutes.
