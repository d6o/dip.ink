# dip.ink — agent self-install

You are an agent that has been asked to install the operator's dip.ink memory
system into your own environment. Follow the section for your runtime, then
confirm to the operator what you installed. If a step's target already exists
with the same content, skip it and say so.

Four pieces get installed, whatever the runtime:

1. **The tools** — access to the memory MCP server.
2. **The usage contract** — global instructions (search before answering,
   capture learnings).
3. **`/recordnotes`** — a command that reviews the session and saves durable
   learnings via `wiki_note_drop`.
4. **The compaction gate** — a hook that blocks compaction until
   `/recordnotes` has run recently. Compaction is where session memory dies;
   this is what makes capture happen *before* the loss.

## 0. Ask for the memory server URL if you don't have it

You need the base URL of the operator's memory server (the docker-compose
default is `http://localhost:8080`; a remote deployment will be something like
`https://memory.<their-domain>`). If the operator didn't tell you, ask.

Set it as a shell variable for the steps below:

```sh
MEMORY_URL=http://localhost:8080   # ← adjust
```

---

## Claude Code

### 1. Register the MCP server

```sh
claude mcp add memory --transport http $MEMORY_URL/mcp
```

Verify: the tools `wiki_search`, `wiki_note_drop`, `graph_answer` (and 7 more)
should now be available. If registration fails, stop and tell the operator —
they may need to put you on the right network/VPN first.

### 2. Install the usage contract

```sh
mkdir -p ~/.claude
curl -fsSL https://raw.githubusercontent.com/d6o/dip.ink/main/AGENTS.md >> ~/.claude/CLAUDE.md
```

If `~/.claude/CLAUDE.md` already contains a section titled "The operator's
memory — usage contract for agents", it's already installed — don't duplicate it.

### 3. Install the /recordnotes skill

```sh
mkdir -p ~/.claude/skills/recordnotes
curl -fsSL https://raw.githubusercontent.com/d6o/dip.ink/main/agent-setup/claude-code/recordnotes/SKILL.md \
  -o ~/.claude/skills/recordnotes/SKILL.md
```

### 4. Install the pre-compaction hook

```sh
mkdir -p ~/.claude/hooks
curl -fsSL https://raw.githubusercontent.com/d6o/dip.ink/main/agent-setup/claude-code/hooks/precompact-recordnotes.py \
  -o ~/.claude/hooks/precompact-recordnotes.py
```

Then register it in `~/.claude/settings.json`. Merge this into the existing
JSON (create the file with just this object if it doesn't exist; if a
`PreCompact` entry already exists, append to its list instead of replacing).
Use a real JSON merge — read, modify, write; do not blindly overwrite the
operator's existing settings:

```json
{
  "hooks": {
    "PreCompact": [
      {
        "matcher": "manual|auto",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/precompact-recordnotes.py",
            "timeout": 10,
            "statusMessage": "Checking note capture before compact"
          }
        ]
      }
    ]
  }
}
```

---

## Pi (pi-coding-agent)

Pi uses native extensions instead of MCP registration — the `memory` extension
declares all ten tools locally (works offline) and talks to the server lazily.

### 1. Install the memory extension (the tools + the usage skill)

```sh
mkdir -p ~/.pi/agent/extensions/memory/skill
base=https://raw.githubusercontent.com/d6o/dip.ink/main/agent-setup/pi/extensions/memory
curl -fsSL $base/index.ts       -o ~/.pi/agent/extensions/memory/index.ts
curl -fsSL $base/package.json   -o ~/.pi/agent/extensions/memory/package.json
curl -fsSL $base/skill/SKILL.md -o ~/.pi/agent/extensions/memory/skill/SKILL.md
```

Point it at the server (skip if using the default `http://localhost:8080/mcp`):
export `MEMORY_MCP_URL=$MEMORY_URL/mcp` in the shell profile Pi runs under.

### 2. Install the usage contract

Append `AGENTS.md` to Pi's global instructions:

```sh
curl -fsSL https://raw.githubusercontent.com/d6o/dip.ink/main/AGENTS.md >> ~/.pi/agent/AGENTS.md
```

(Same dedup rule: skip if the section is already there.)

### 3. Install /recordnotes (prompt) + the compaction/exit gate (extension)

```sh
mkdir -p ~/.pi/agent/prompts
curl -fsSL https://raw.githubusercontent.com/d6o/dip.ink/main/agent-setup/pi/prompts/recordnotes.md \
  -o ~/.pi/agent/prompts/recordnotes.md
curl -fsSL https://raw.githubusercontent.com/d6o/dip.ink/main/agent-setup/pi/extensions/recordnotes.ts \
  -o ~/.pi/agent/extensions/recordnotes.ts
```

The prompt becomes the `/recordnotes` command. The extension gates
`session_before_compact` (cancels compaction and queues `/recordnotes` when the
ack is stale — never blocking hard overflow) and wraps `/exit`, `/bye`, and
`/done` so graceful exits flush notes first.

---

## Verify the loop end to end (both runtimes)

1. `graph_answer("what is this memory system's note inbox called")` — any
   non-error response proves the read path.
2. Call `wiki_note_drop` with slug `install-verification` and a short note
   saying this machine was set up (include the machine name/OS and which
   runtime you installed for). `ok: true` + `pushed: true` proves the write path.
3. Run `/recordnotes` once — it should review, possibly drop nothing new, and
   write the compact-ack marker.
4. Report to the operator: what you registered, what you installed, and the
   note-drop result.

## What this changes about your behavior

From now on, in every session (per the contract you installed):

- **Search the memory before answering** anything operator-specific —
  `graph_answer` first for factual questions.
- **Capture learnings as you go** with `wiki_note_drop` — don't wait to be asked.
- **Run `/recordnotes`** before compaction or exit (the hooks will remind you)
  or whenever the operator asks to save the session's learnings.
