---
name: recordnotes
description: |
  Capture wiki-worthy learnings from the current session via wiki_note_drop
  so they are committed to the operator's memory repo and ingested later.
  Use when asked to "record notes", "flush notes", "save learnings", or before
  compacting a session that discovered useful context.
allowed-tools:
  - mcp__memory__wiki_note_drop
  - Bash
  - Read
  - Write
  - AskUserQuestion
triggers:
  - recordnotes
  - record notes
  - flush notes
  - save learnings
  - before compact
---

## Goal

Preserve durable learnings from the current session as one or more notes in the memory's `notes/` inbox, through the `wiki_note_drop` MCP tool.

Do **not** update the wiki directly from this skill. This skill only writes inbox notes for the curator to ingest later.

## What is worth recording

Record things future sessions would be glad to know without re-deriving:

- tools, services, deploys, workflows, environment wiring
- architecture discoveries and runtime behavior
- decisions and their reasoning
- debugging findings, failure modes, and operational gotchas
- useful commands, file paths, dashboards, URLs, and artifact locations

Skip ephemeral chatter, obvious code diffs with no extra insight, and things already fully captured in code unless the **why** is non-obvious.

## Workflow

1. Review the current session and identify durable learnings worth preserving.
2. Group them into **1-3 coherent notes by topic**. Prefer one topic per note.
3. If nothing durable was learned, say so briefly — but still write the compact-ack marker described below.
4. For each note you do create, call `wiki_note_drop` with a short kebab-case slug (no timestamp; the server prepends UTC time) and `note_md` containing this frontmatter:

```yaml
---
captured: <ISO 8601 datetime>
session: <1-line description of what you were doing>
topic: <1-5 word topic>
---
```

5. The note body should over-explain. The curator cannot read the chat transcript later. Include:
   - what was learned
   - why it matters
   - exact file paths / commands / URLs / artifact names when relevant
   - references to any created attachments using relative paths
6. Never include credentials, tokens, or passwords. If relevant, say where they live instead.
7. If a small text artifact would help, pass it as a `wiki_note_drop` attachment and reference it. This is optional.
8. At the end, always write/update the compact-ack marker for the **current working directory** so the pre-compact reminder allows compact for the next 30 minutes.

If `wiki_note_drop` is unavailable or fails, report the failure explicitly — a note that didn't reach the server is not recorded. Do not silently skip.

## Compact-ack marker

After you finish reviewing and note-writing, run this snippet with the `Bash` tool:

```bash
python3 - <<'PY'
import hashlib, json, os, pathlib, time
cwd = os.getcwd()
ack_dir = pathlib.Path.home() / '.claude' / 'hooks' / 'recordnotes-acks'
ack_dir.mkdir(parents=True, exist_ok=True)
key = hashlib.sha256(cwd.encode('utf-8')).hexdigest()
ack_path = ack_dir / f'{key}.json'
ack_path.write_text(json.dumps({
    'cwd': cwd,
    'ts': time.time(),
    'reviewed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
}, indent=2) + '\n')
print(str(ack_path))
PY
```

Do this even if no new note was necessary. The marker means "this directory was reviewed for note capture recently," not "a note was definitely created."

## Response format

When done, report either:

- the `wiki_note_drop` folder/commit/URL values, grouped by topic, or
- that no new note was needed

Then say that compact has been acknowledged for this working directory for 30 minutes.
