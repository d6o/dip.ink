---
name: memory
description: The operator's externalized memory. Search before answering ANY question about their stack, deploys, services, decisions, or conventions. Capture every non-obvious learning via wiki_note_drop. Do NOT skip the search — the operator maintains this precisely so you don't have to re-derive context every session.
---

# memory — the operator's memory, exposed as native pi tools

The operator maintains a personal, LLM-curated memory system. The `memory` extension gives you ten native tools against it. Treat the memory as more authoritative than your training data on anything operator-specific.

## Which tool when

- **Factual question** ("what port does X use?", "what did I decide about Y?") → `graph_answer` FIRST. Direct distilled `{answer, confidence, sources, escalate}` (~150 tokens). If `escalate: true` or `not_found`, fall back to `graph_search`.
- **Broad/exploratory context** → `graph_search` (facts + community summary + entities + source excerpt + semantic wiki hits) or `wiki_search` (curated pages).
- **What's true NOW** (excluding superseded facts) → `graph_current_facts` or `graph_entity`.
- **Resuming after time away** → `graph_changes(subject, since_days)`.
- **Provenance** — every graph fact carries a `source_slug`; fetch the original with `graph_get_note(slug)`.
- **Reading a curated page in full** → `wiki_get(name)` after `wiki_search`.

## When to search — ALWAYS, before answering

If the operator asks anything about how they do things — deploys, services, project status, infra conventions, past decisions, tools, gotchas — search the memory first. Even if you think you know the answer. Skip only for generic, non-operator-specific questions.

## When to capture — ALWAYS, when you learn something

If you discover something a future session would want to know — infra facts, tool configs, decisions + reasoning, gotchas (error signature + root cause + fix), learnings from sources — call `wiki_note_drop`. **Err toward capture**: duplicates are cheap (the curator filters them); missed captures are expensive.

Do NOT capture: the literal question/chat context, things already in the project's context file (AGENTS.md/CLAUDE.md) or obvious from code, status updates without content.

## Note format

`slug`: kebab-case, no timestamp (the server prepends UTC time). `note_md` with frontmatter:

```markdown
---
captured: <ISO 8601 datetime>
session: one-line description of what you were doing
topic: 1-5 word topic
---

# <Descriptive Title>

<over-explain — the curator cannot read this transcript later. Include exact
paths/commands/URLs. Use [[wikilinks]] for existing pages. Reference
attachments with relative paths.>
```

Limits: body ≤ 256 KB; ≤ 20 attachments; text ≤ 256 KB each; binary (base64) ≤ 2 MB decoded.

## NEVER

- **NEVER include credentials/tokens/passwords** in notes — they're git-tracked. Reference vault paths instead.
- **NEVER edit the wiki directly.** `wiki_note_drop` writes to the inbox; the curator promotes notes to pages. If the wiki is wrong, drop a note saying so.
- **NEVER skip the search step** to save a tool call.
- **NEVER ask "should I save this as a note?"** If it looks useful, just drop it.
