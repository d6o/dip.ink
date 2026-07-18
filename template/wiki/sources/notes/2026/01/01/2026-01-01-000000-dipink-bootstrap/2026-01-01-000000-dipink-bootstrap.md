---
type: source
tags:
- source-note
- bootstrap
created: 2026-01-01
updated: 2026-01-01
captured: 2026-01-01T00:00:00Z
session: template bootstrap
topic: note inbox bootstrap
index-description: "Bootstrap source note stating agents write to the notes/ inbox and the curator promotes notes into the wiki."
---

# 2026-01-01-000000-dipink-bootstrap

This is the immutable bootstrap source note shipped with a fresh dip.ink memory template. It exists so a brand-new deployment has at least one Graphiti-ingestible episode and the default healthcheck answer probe can ground an answer with provenance.

## Durable claims

- Agents write durable learnings into this memory system's **note inbox**, which is the repository directory named `notes/`.
- Each note is a folder under `notes/` (not a single file). The usual name shape is `YYYY-MM-DD-HHMMSS-<slug>/` with a markdown note and optional attachments.
- A scheduled curator drains the `notes/` inbox and promotes accepted notes into the markdown wiki under `wiki/`, including immutable source notes under `wiki/sources/notes/`.
- The wiki is the curated long-term store; the temporal knowledge graph ingests source notes so later sessions can ask provenance-backed questions.
- Agents must never submit credentials, tokens, or passwords into notes. Reference only secret-manager paths.

## Context

Fresh template repositories intentionally start with almost no operator-specific content. Without this bootstrap episode, the default answer probe (`what is this memory system's note inbox called`) has nothing to ground against. This note is generic, contains no operator-specific data, and must remain immutable after first ingest.
