# notes/ — inbox

Inbox for notes dropped by agent sessions (usually via the `wiki_note_drop` MCP tool). The drop protocol is defined in `AGENTS.md` in the dip.ink repo.

Entries here are **transient**: drained by the `/processnotes` skill (see `.claude/commands/processnotes.md`) and deleted from disk after ingest. Git history preserves them.

## Format agents should use

Each note is a **folder**, not a single file. This lets agents include attachments (screenshots, logs, config snippets) without naming collisions.

```
notes/
  2026-04-22-141500-vault-deployed/
    NOTE.md              # required — the note itself
    deploy.log           # optional — any supporting artifact
    screenshot.png       # optional — referenced from NOTE.md
  2026-04-22-153022-git-server-setup/
    NOTE.md
```

Folder name: `YYYY-MM-DD-HHMMSS-<short-slug>/`.

`NOTE.md` frontmatter:
```yaml
---
captured: 2026-04-22T14:55:00
session: what the session was doing when it learned this
topic: 1–5 word topic
---
```

`NOTE.md` body: freeform markdown. Over-explain rather than under-explain — the processor can't read the capturing session's transcript. Reference attachments with relative paths: `![](./diagram.png)`, `see ./output.log`.

**Never include credentials.** Reference where they live instead.

Duplicates are fine; missed captures are expensive.
