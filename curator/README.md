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
| Inbox batcher (oldest-4 live, rest deferred) | `scripts/processnotes-prepare-inbox.sh` |
| The curator prompt | `.pi/prompts/processnotes-auto.md` |
| Validators | `scripts/wikilint.py` + `wikiindex.py` + `logrotate.py` + `wikidistill.py` |

## pi-runner (this directory)

`pi-runner/` is the container image the workflows run in. It wraps the
[Pi coding agent](https://github.com/badlogic/pi-mono) for CI use:

1. drops privileges to an unprivileged user,
2. runs Pi headless (`--no-session --mode json`) with the prompt from
   `PROMPT_PATH`, prefixed by a CI preamble (no git ops, no questions),
3. renders Pi's JSON event stream as concise CI telemetry,
4. on changes: runs `VALIDATOR`, then commits, fetches, rebases, and pushes
   with bounded retries.

The image is published as `ghcr.io/d6o/dip.ink/pi-runner` by this repo's CI.

### Environment

| Var | Default | |
|---|---|---|
| `PROMPT_PATH` | (required) | prompt file, relative to the repo checkout |
| `PI_API_KEY` | (required) | LLM key for the agent |
| `PI_PROVIDER` / `PI_MODEL` | `openai` / `gpt-4.1-mini` | any provider Pi supports |
| `PI_MODELS_JSON` | — | ephemeral custom provider config (OpenAI-compatible endpoints) |
| `WIKI_REPO_TOKEN` | (required to push) | HTTPS token; in GitHub Actions use `x-access-token:${{ secrets.GITHUB_TOKEN }}` |
| `VALIDATOR` | `true` | shell command that must pass before commit |
| `COMMIT_MESSAGE`, `GIT_USER_NAME`, `GIT_USER_EMAIL`, `GIT_BRANCH` | sensible defaults | |

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
- **Probe before batch.** A 1-token completion against the LLM endpoint before
  every batch after the first; a dead provider stops the run instead of
  burning the hour.
- **Optimistic with receipts.** No human in the loop; non-routine decisions go
  to `wiki/Curator review queue.md` (see the prompt for the exact three buckets).
- **The validator owns correctness.** The agent never runs lint/index/rotate
  itself; the runner runs the full chain and refuses to commit on failure.

## Testing

`template/scripts/test-processnotes-supervisor.sh` covers the supervisor's
batching, budget, probe-failure, and no-op semantics with fakes — run it from
the template (or your memory repo) root:

```sh
bash scripts/test-processnotes-supervisor.sh
```
