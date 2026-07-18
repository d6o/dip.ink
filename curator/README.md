# curator — the scheduled agent that maintains the wiki

The curator is the piece that turns the `notes/` inbox into the wiki. It is
**required**: without it, notes accumulate and the compiled layer (wiki pages,
which also feed `graph_search`'s fusion hits) never forms.

It runs **on the memory repo itself** (the one you created from `template/`),
as CI workflows — not in docker-compose — because it needs to commit and push
to that repo. Everything it needs ships inside the template:

| Piece | Where (in your memory repo) |
|---|---|
| Hourly curation workflow | `.github/workflows/curator.yml` |
| Weekly synthesis workflow | `.github/workflows/synthesis.yml` |
| Daily review-queue workflow | `.github/workflows/reviewqueue.yml` |
| Supervisor (batching, budget, probes) | `scripts/processnotes-supervisor.sh` |
| Inbox batcher (oldest-4 live, rest deferred; `.blocked/` excluded) | `scripts/processnotes-prepare-inbox.sh` |
| The curator prompt | `.pi/prompts/processnotes-auto.md` |
| Validators | `scripts/wikilint.py` + `wikiindex.py` + `logrotate.py` + `wikidistill.py` |

All three workflows share one concurrency group:

```yaml
concurrency:
  group: memory-repo-writer
  cancel-in-progress: false
```

so they never race on `wiki/log.md` or other shared pages. Public images are
pinned to `ghcr.io/d6o/dip.ink/pi-runner:v0.1.5`.

## pi-runner (this directory)

`pi-runner/` is the container image the workflows run in. It wraps the
[Pi coding agent](https://github.com/badlogic/pi-mono) for CI use:

1. drops privileges to an unprivileged user,
2. runs Pi headless (`--no-session --mode json`) with the prompt from
   `PROMPT_PATH`, prefixed by a CI preamble (no git ops, no questions),
3. renders Pi's JSON event stream as concise CI telemetry,
4. on changes: runs `VALIDATOR`, then commits, fetches, rebases, and pushes
   with bounded retries.

The image is published as `ghcr.io/d6o/dip.ink/pi-runner` by this repo's CI
(semver + SHA tags; no mutable `latest`).

### Environment

| Var | Default | |
|---|---|---|
| `PROMPT_PATH` | (required) | prompt file, relative to the repo checkout |
| `PI_API_KEY` | (required) | LLM key for the agent |
| `PI_PROVIDER` / `PI_MODEL` | `openai` / `gpt-4.1-mini` | any provider Pi supports |
| `PI_MODELS_JSON` | — | ephemeral custom provider config (OpenAI-compatible endpoints). Set as a repo Actions variable; the runner writes it to Pi's models config. |
| `WIKI_REPO_TOKEN` | (required to push) | HTTPS token; in GitHub Actions use `x-access-token:${{ secrets.GITHUB_TOKEN }}` |
| `VALIDATOR` | `true` | shell command that must pass before commit |
| `COMMIT_MESSAGE`, `GIT_USER_NAME`, `GIT_USER_EMAIL`, `GIT_BRANCH` | sensible defaults | |

`PI_MODELS_JSON` is **curator/Pi-runner configuration only**. It is not a
memory-server environment variable and is not injected into Compose/k8s.

## Swapping the agent

Nothing in the pipeline is Pi-specific except the runner. To use another
headless agent (Claude Code, Codex CLI, ...):

- keep the supervisor and set `CURATOR_RUNNER_BIN` to your own entrypoint that
  (a) runs the agent with `.pi/prompts/processnotes-auto.md` as the task,
  (b) validates, commits, rebases, pushes on success — the supervisor only
  cares that HEAD advances on success and the exit code is 0;
- or replace the workflow's run step entirely and keep the prompt + validators.

## Design properties worth keeping

- **Fresh session per sub-batch of 4.** Bounded context, bounded blast radius.
- **Durable progress.** Commit+push per sub-batch; a later crash loses nothing.
- **Empty-inbox short circuit.** The supervisor checks the inbox before any
  hourly LLM preflight; empty runs make zero provider calls.
- **Provider-aware preflight.** OpenAI-compatible providers get a 1-token probe
  before later batches; Anthropic/native/custom providers configured via
  `PI_MODELS_JSON` skip the HTTP preflight instead of failing it.
- **Blocked quarantine.** FLAGged, malformed, and already-ingested folders move
  to `notes/.blocked/` with a receipt and never re-enter the live oldest-first
  queue. The inbox preparer parses required YAML frontmatter before provider
  use, so one malformed note cannot poison its valid neighbors.
- **Optimistic with receipts.** No human in the loop; non-routine decisions go
  to `wiki/Curator review queue.md`.
- **No automatic secret scanning.** Agents must never submit credentials; the
  private repo and gated network are the perimeter. The curator does not scan
  or redact secrets and does not claim to make a committed secret safe.
- **The validator owns correctness.** The agent never runs lint/index/rotate
  itself; the runner runs the full chain and refuses to commit on failure.
  Staged diff checks still reject trailing spaces and space-before-tab, but
  tolerate harmless extra blank lines at EOF so valid Markdown batches are not
  discarded for formatting-only drift.

## Testing

`template/scripts/test-processnotes-supervisor.sh` covers the supervisor's
batching, budget, probe-failure, blocked/malformed exclusion, and no-op semantics with
fakes — run it from the template (or your memory repo) root:

```sh
bash scripts/test-processnotes-supervisor.sh
```
