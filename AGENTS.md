# The operator's memory — usage contract for agents

> Install this file globally so EVERY agent session follows it: append it to
> `~/.claude/CLAUDE.md` (Claude Code), `~/.config/AGENTS.md` (agents honoring
> the AGENTS.md convention), or your agent's system prompt. It is the contract
> that makes the memory compound instead of decay.

The operator maintains a personal, LLM-curated memory system — the canonical record of their tools, deploys, infra conventions, project context, decisions, and learnings. As an agent, you reach it through **one MCP server** exposing two families of tools:

| Family | Tools | What it's for |
|---|---|---|
| **wiki** | `wiki_search`, `wiki_get`, `wiki_backlinks`, `wiki_note_drop` | Semantic search over curated wiki pages + THE write path (note capture) |
| **graph** | `graph_answer`, `graph_search`, `graph_get_note`, `graph_entity`, `graph_current_facts`, `graph_changes` | Temporal knowledge graph: direct answers, current-vs-superseded facts, what-changed diffs |

Storage is a private git repo of markdown notes and wiki pages. You generally don't clone it — you go through MCP — but knowing the data lives in git matters when something seems out of date.

**You must use these tools any time you are doing work for the operator.** Treat the memory as more authoritative than your training data on anything operator-specific. When it conflicts with general knowledge, the memory wins.

## Setup (one-time per machine)

```sh
claude mcp add memory --transport http http://<your-memory-host>:8080/mcp
```

If the tools aren't in your tool list and registration fails, you are not on the operator's network and this contract does not apply.

## Which tool when

- **Factual question** ("what port does X use?", "what did I decide about Y?") → `graph_answer` FIRST. It returns a distilled `{answer, confidence, sources, escalate}` (~150 tokens) instead of a fat search packet. If it returns `escalate: true` or `not_found`, fall back to `graph_search`.
- **Broad/exploratory context** ("what do I know about X?") → `graph_search` (facts + community summary + entities + source excerpt + semantic note hits) or `wiki_search` (curated pages).
- **What's true NOW** (excluding superseded facts) → `graph_current_facts` or `graph_entity`.
- **Resuming after time away** → `graph_changes(subject, since_days)` — one call instead of five searches.
- **Provenance** — every graph fact carries a `source_slug`; fetch the original note with `graph_get_note(slug)`.
- **Reading a curated page in full** → `wiki_get(name)` after `wiki_search`.

## When to search — ALWAYS, before answering

If the operator asks **anything** about how they do things — deploys, services, project status, infra conventions, past decisions, tools, gotchas — call `graph_answer` or `wiki_search` first. Even if you think you know the answer. The memory captures decisions and reasoning your training data does not have.

Skip the search only for generic, non-operator-specific questions (e.g. "what does this Python error mean").

## When to capture — ALWAYS, when you learn something

If during your work you discover something the operator or a future session would want to know — a fact about their infra, a tool, a deploy, a workflow, a decision, a gotcha, a learning from a source — call `wiki_note_drop(...)`. Do not store useful information only in chat.

**Bar for "worth capturing" is low. Err toward capture.** Duplicate notes are cheap (the curator filters them); missed captures are expensive.

Capture when you learn:

- A tool the operator uses, how it's configured, where its state lives.
- A deploy or service detail — endpoints, manifests, credentials' vault paths (never the values).
- A decision and the reasoning behind it.
- A learning from an article, paper, or doc the operator pointed you at.
- A gotcha that bit you (or almost did) — error signature + root cause + fix.
- Operational facts about their machines or environments.
- Anything you would want to be able to look up later.

Do NOT capture:

- The literal question or chat context — only the substantive learning.
- Things already documented in CLAUDE.md or obviously derivable from the code.
- Status updates without content ("did X today" — what's the learning?).

## How to format a note

Pass `note_md` as the full source-note body, including YAML frontmatter:

```markdown
---
captured: 2026-05-14T17:00:00Z
session: one-line description of what you were doing
topic: 1-5 word topic
---

# <Descriptive Title>

<freeform markdown body — write as much context as you have. The
curator session that ingests this cannot read your transcript, so
over-explain rather than under-explain.>

## <Subsections as needed>

- Use [[wikilinks]] when you reference existing wiki pages.
- Use code fences for commands, error output, log excerpts.
- Cite sources (URLs, file paths, shell output) when relevant.
- Reference vault paths for any credential you mention.
```

Slug rules: `^[a-z0-9][a-z0-9-]{0,63}$` — lowercase, hyphens, ≤ 64 chars.

Size limits: source-note body ≤ 256 KB, ≤ 20 attachments, text attachments ≤ 256 KB each, binary (base64) ≤ 2 MB decoded.

### Attachments

- Text artifacts (logs, configs, transcripts, yaml, json): `attachments={"deploy.log": "...", "config.yaml": "..."}` — string content.
- Binary artifacts (screenshots, pdfs, diagrams): `binary_attachments={"screenshot.png": "<base64>"}`.

Reference attachments from the note with relative links: `see ./deploy.log`.

## NEVER

- **NEVER include credentials, tokens, passwords, API keys, JWTs, or SSH private keys** in `note_md` or attachments. Notes are git-tracked. Reference where the secret lives instead: `"token in the vault at /services/<service>/<KEY>"`. If you suspect a value might be sensitive, redact it before passing to the tool.
- **NEVER edit the wiki directly via these tools.** `wiki_note_drop` writes to `notes/`, the inbox. The curation pass is the only thing that promotes a note to a wiki page. If the wiki is wrong, drop a note saying so.
- **NEVER skip the search step** to save a tool call. The cost of a search is milliseconds; the cost of a stale or wrong answer the memory has corrected is much higher.
- **NEVER ask "should I save this as a note?"** If it looks useful, just drop it. Don't add friction.

## /recordnotes — the end-of-session flush

If `/recordnotes` is installed (see INSTALL_FOR_AGENTS.md), run it before compacting or ending a session that learned anything: it reviews the session, saves durable learnings via `wiki_note_drop`, and acknowledges compaction for 30 minutes. A pre-compaction hook may block compaction until it has run — that is intentional: compaction is where unsaved session memory dies.

## Summary in one line

If you're working for the operator and you didn't search the memory, you skipped a step. If you learned something and didn't call `wiki_note_drop`, you forgot a step. Both should be reflexive.
