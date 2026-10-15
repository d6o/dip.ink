# curator — the scheduled agent that maintains the wiki

The curator is the piece that turns the `notes/` inbox into the wiki. It is
**required**: without it, notes accumulate and the compiled layer (wiki pages,
which also feed `graph_search`'s fusion hits) never forms.

It runs **on the memory repo itself** (the one you created from `template/`),
as CI workflows — not in docker-compose — because it needs to commit and push
to that repo. Everything it needs ships inside the template:

| Piece | Where |
|---|---|
| Hourly curation workflow | memory repo: `.github/workflows/curator.yml` |
| Weekly synthesis workflow | memory repo: `.github/workflows/synthesis.yml` |
| Daily review-queue workflow | memory repo: `.github/workflows/reviewqueue.yml` |
| Supervisor (batching, budget, probes) | image: `/opt/dip.ink/scripts/processnotes-supervisor.sh` (source: `curator/scripts/`) |
| Inbox batcher (oldest-4 live, rest deferred; `.blocked/` excluded) | image: `/opt/dip.ink/scripts/processnotes-prepare-inbox.sh` |
| The curator prompt | image: `/opt/dip.ink/prompts/processnotes-auto.md` (source: `curator/prompts/`) |
| Validators | image: `/opt/dip.ink/scripts/wikilint.py` + `wikiindex.py` + `logrotate.py` + `wikidistill.py` |

The memory repo itself is **data-only**: notes, wiki pages, and thin workflow
config pinning an immutable `pi-runner` tag. All curator code ships in the
image and upgrades via a dip.ink release, not repo-by-repo copies.

All three workflows share one concurrency group:

```yaml
concurrency:
  group: memory-repo-writer
  cancel-in-progress: false
```

so they never race on `wiki/log.md` or other shared pages. Public images are
pinned to `ghcr.io/d6o/dip.ink/pi-runner:v0.1.49`.

## pi-runner (this directory)

`pi-runner/` is the container image the workflows run in. It wraps the
[Pi coding agent](https://github.com/badlogic/pi-mono) for CI use:

1. drops privileges to an unprivileged user,
2. runs Pi headless (`--no-session --mode json`) with the prompt from
   `PROMPT_PATH`, prefixed by a CI preamble (no git ops, no questions),
3. renders Pi's JSON event stream as concise CI telemetry,
4. on changes: runs `VALIDATOR`, then starts one repair-only Pi invocation after a validation failure,
5. runs the validator one final time, then commits, fetches, rebases, and pushes with bounded retries.

The supervisor rejects dual-root note identities before preparation changes.
It exposes exactly the oldest configured live batch and records a private metadata manifest outside the checkout.
The parent retains its digest. The runner checks selected identities, held paths, raw bytes, and existing source bytes after Pi.
It checks the boundary after every validator return, before another Pi call, and before commit.
Restored preparation paths or excess live inputs stop the workflow.
The supervisor accepts only `NOTES_DIR=notes`, which matches its prompt and live count.
The standalone guard CLI can use another inbox root.
Source-schema frontmatter fields and the first H1 may change during a selected canonical move.
Preserve `captured`, `session`, `topic`, `name`, `title`, other original metadata, the remaining source body, and attachments.
A selected input with a canonical copy must leave the live inbox after Pi.
A pre-Pi check permits a captured duplicate so Pi can use a supported block or drop outcome.
Live selected inputs and canonical moves check resolved attachment references.
These checks use folder-relative identity for local targets and repository-relative identity for other in-repository targets.
File targets retain bytes, type, and mode. Directory targets retain path, type, and mode.
The check does not hash external directory contents recursively.
Block retains complete-folder bytes, modes, link text, and a bounded receipt under the existing quarantine policy.
A relocated block link need not resolve to the same target.
Block does not add protection for outside-folder target contents. Raw and prior source trees remain independently protected.
The manifest uses version 6 and requires a fresh capture from the same toolchain.
Versions 1–5 fail closed.

The archive classifier distinguishes a canonical root from its retained original.
A direct same-slug child with `NOTE.md` is part of its canonical parent when that parent has a regular `<slug>.md`.
The child must not contain its own `<slug>.md`.
The complete source snapshot still protects every child's path, bytes, type, and mode.
Separate canonical documents and independent legacy roots still count as separate identities.
This structural rule does not authorize archive edits, deletion, relinking, or changes to source eligibility.
Frontmatter checks also hash resolved YAML node dependencies with the installed PyYAML parser.
A mutable schema field cannot change an original protected alias or merge dependency.
The guard also compares effective inherited fields, so a new explicit key cannot shadow their original values.
The parser does not construct custom objects. It limits protected graph traversal to 50,000 visits and depth 64.
Merge resolution also uses the traversal budget and memo, rather than an unbounded expansion.
Shared NaN identity stays separate from independent non-reflexive NaN fields.
The hashes retain every protected value in each independent NaN group.
Dependency hashes also retain stable YAML anchor names, so equal non-reflexive keys cannot exchange protected alias associations.
Anchor names enter only the hash input. Interpreter addresses never enter the manifest.
Scalar keys use safe built-in constructors only. Cyclic protected metadata fails safely. The manifest stores hashes, not resolved values.
The H1 scan recognizes backtick and tilde fences, not complete Markdown syntax.
The guard is a workflow consistency check, not a permission fence against a hostile process.

The repair invocation receives bounded validator diagnostics. It cannot add paths outside the failed batch.
Its prompt prohibits new note processing. Provider, Git, and other failures do not start a repair invocation.

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
| `VALIDATOR_DIAGNOSTIC_BYTES` | `12000` | maximum validator output bytes included in the one repair prompt |
| `INBOX_PREPARED_STATE` / `INBOX_PREPARED_SHA256` | supervisor-owned | private prepared manifest and parent-retained digest; required for processnotes |
| `INBOX_GUARD_BIN` | image helper | the guard from the shared toolchain |
| `NOTES_DIR` | `notes` | fixed inbox root for the supervisor; other values reject before preparation |
| `COMMIT_MESSAGE`, `GIT_USER_NAME`, `GIT_USER_EMAIL`, `GIT_BRANCH` | sensible defaults | |

`PI_MODELS_JSON` is **curator/Pi-runner configuration only**. It is not a
memory-server environment variable and is not injected into Compose/k8s.

## Swapping the agent

Nothing in the pipeline is Pi-specific except the runner. To use another
headless agent (Claude Code, Codex CLI, ...):

- keep the supervisor and set `CURATOR_RUNNER_BIN` to your own entrypoint that
  (a) runs the agent with `/opt/dip.ink/prompts/processnotes-auto.md` as the task,
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
  itself. The runner permits one repair-only Pi invocation after a validation
  failure. It runs the validator once more and refuses to commit on failure.
  Staged diff checks still reject trailing spaces and space-before-tab, but
  tolerate harmless extra blank lines at EOF.

## Testing

`curator/scripts/test-processnotes-supervisor.sh` covers the supervisor's
batching, budget, probe-failure, blocked/malformed exclusion, and no-op semantics with
fakes — run it from the dip.ink repo root:

```sh
bash curator/scripts/test-processnotes-supervisor.sh
```

The new boundary regressions use synthetic payloads and disposable local Git repositories:

```sh
python3 curator/scripts/test-processnotes-batch-guard.py
bash curator/pi-runner/tests/test-prepared-boundary.sh
```

They reject restored, dual-root, excess, or modified protected inputs without deleting either copy.
Supported canonical moves, drops, blocks, and unchanged no-op inputs remain available.
