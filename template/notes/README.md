# notes/ — inbox

Inbox for notes dropped by agent sessions (usually via the `wiki_note_drop` MCP tool). The drop protocol is defined in `AGENTS.md` in the dip.ink repo.

Live entries here are **transient**: `/processnotes` moves accepted folders to canonical `wiki/sources/notes/YYYY/MM/DD/...` paths. FLAGged and already-ingested folders instead move to the terminal `notes/.blocked/` quarantine. Git history preserves every transition.

## Format agents should use

Each note is a **folder**, not a single file. This lets agents include attachments (screenshots, logs, config snippets) without naming collisions.

```
notes/
  2026-04-22-141500-service-deployed/
    NOTE.md              # required — the note itself
    deploy.log           # optional — any supporting artifact
    screenshot.png       # optional — referenced from NOTE.md
  2026-04-22-153022-git-server-setup/
    NOTE.md
  .deferred/             # supervisor-held future batches
  .blocked/              # terminal; never returned to live/deferred batching
    2026-04-22-120000-malformed-note/
      NOTE.md            # original bytes preserved
      artifact.bin       # original bytes preserved
      BLOCKED.md         # bounded reason/timestamp receipt added by the curator
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

**Never include credentials, tokens, or passwords.** Reference only the corresponding secret-manager path.

Duplicates are fine; missed captures are expensive. Exact duplicates already named in an ingest log are moved to `.blocked/` with reason `already-ingested` so they cannot poison oldest-first batches. A blocked folder is immutable audit evidence: do not edit its original note or attachments, and do not move it back into live or deferred batching.
