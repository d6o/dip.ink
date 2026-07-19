#!/usr/bin/env bash
set -euo pipefail

log() { echo "[pi-runner] $*" >&2; }

# CI containers start as root so the checkout can be prepared, but the agent
# itself should run as an unprivileged user.
if [[ $(id -u) -eq 0 ]]; then
  RUNNER_UID="${PI_RUNNER_UID:-1001}"
  RUNNER_GID="${PI_RUNNER_GID:-1001}"
  if ! getent group "$RUNNER_GID" >/dev/null 2>&1; then
    groupadd -g "$RUNNER_GID" runner 2>/dev/null || true
  fi
  if ! getent passwd "$RUNNER_UID" >/dev/null 2>&1; then
    useradd -m -u "$RUNNER_UID" -g "$RUNNER_GID" -s /bin/bash runner 2>/dev/null || true
  fi
  RUNNER_HOME=$(getent passwd "$RUNNER_UID" | cut -d: -f6)
  log "dropping privileges to uid=$RUNNER_UID home=$RUNNER_HOME"
  chown -R "$RUNNER_UID:$RUNNER_GID" "$PWD" "$RUNNER_HOME" 2>/dev/null || true
  export HOME="$RUNNER_HOME"
  exec gosu "$RUNNER_UID:$RUNNER_GID" /bin/bash "$0" "$@"
fi

: "${PROMPT_PATH:?PROMPT_PATH is required (absolute, e.g. /opt/dip.ink/prompts/..., or repo-relative)}"

: "${PI_API_KEY:?PI_API_KEY is required}"
export PI_API_KEY

PI_PROVIDER="${PI_PROVIDER:-openai}"
PI_MODEL="${PI_MODEL:-gpt-4.1-mini}"
# Medium is the generic default: enough reasoning for multi-file agent work
# without spending the extra latency of high thinking on routine automation.
PI_THINKING="${PI_THINKING:-medium}"
VALIDATOR="${VALIDATOR:-true}"
COMMIT_MESSAGE="${COMMIT_MESSAGE:-chore(agent): update from $(basename "$PROMPT_PATH" .md)}"
GIT_USER_NAME="${GIT_USER_NAME:-pi-runner[bot]}"
GIT_USER_EMAIL="${GIT_USER_EMAIL:-pi-runner@localhost}"
GIT_BRANCH="${GIT_BRANCH:-main}"
PI_BIN="${PI_BIN:-pi}"
PI_EVENT_LOGGER="${PI_EVENT_LOGGER:-/opt/pi-runner/pi-event-log.py}"
export PI_OFFLINE="${PI_OFFLINE:-1}"
export PI_SKIP_VERSION_CHECK="${PI_SKIP_VERSION_CHECK:-1}"
export PI_TELEMETRY="${PI_TELEMETRY:-0}"

if [[ ! -f "$PROMPT_PATH" ]]; then
  log "error: prompt file not found: $PROMPT_PATH"
  exit 2
fi
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log "error: current directory is not a git worktree"
  exit 2
fi
if [[ ! -x "$PI_EVENT_LOGGER" ]]; then
  log "error: event logger is not executable: $PI_EVENT_LOGGER"
  exit 2
fi

# Use an empty, per-run Pi home so CI cannot inherit packages, extensions,
# credentials, or settings from a mounted user profile. --approve below is the
# explicit trust decision for project-local context and settings.
PI_CONFIG_IS_TEMP=0
if [[ -z "${PI_CODING_AGENT_DIR:-}" ]]; then
  PI_CODING_AGENT_DIR=$(mktemp -d)
  PI_CONFIG_IS_TEMP=1
fi
export PI_CODING_AGENT_DIR

# Workflow-supplied custom providers live only for this ephemeral invocation.
# Validate before writing so malformed JSON fails before the model starts.
if [[ -n "${PI_MODELS_JSON:-}" ]]; then
  if ! printf '%s' "$PI_MODELS_JSON" | jq -e . > "$PI_CODING_AGENT_DIR/models.json"; then
    log "error: PI_MODELS_JSON is not valid JSON"
    exit 2
  fi
  chmod 600 "$PI_CODING_AGENT_DIR/models.json"
fi

cleanup() {
  if [[ "$PI_CONFIG_IS_TEMP" -eq 1 ]]; then
    rm -rf "$PI_CODING_AGENT_DIR"
  fi
}
trap cleanup EXIT

git config user.name "$GIT_USER_NAME"
git config user.email "$GIT_USER_EMAIL"
git config --global --add safe.directory "$PWD"

INITIAL_STATUS=$(git status --porcelain)
if [[ -n "$INITIAL_STATUS" ]]; then
  log "warn: workspace has pre-existing changes; this is unusual in CI"
  echo "$INITIAL_STATUS" >&2
fi

PREAMBLE=$(cat <<'EOF'
You are running in CI inside a container with a fresh repository checkout.

Conventions for this run:
- Make all file changes required by the task; the runner commits and pushes after you exit.
- Update task state files so a later run does not repeat completed work.
- If there is nothing to do, exit cleanly without writing files; a zero diff is a successful no-op.
- Do not run git commit, git push, or create/switch branches. The runner owns version control.
- Use only repository-local files, the shared curator toolchain under /opt/dip.ink/, and the built-in read, bash, edit, and write tools. No external MCP services are configured.

Below is the task prompt.

---
EOF
)
FULL_PROMPT="$PREAMBLE"$'\n'"$(cat "$PROMPT_PATH")"

log "running pi provider=$PI_PROVIDER model=$PI_MODEL thinking=$PI_THINKING prompt=$PROMPT_PATH"
set +e
"$PI_BIN" \
  --provider "$PI_PROVIDER" \
  --model "$PI_MODEL" \
  --thinking "$PI_THINKING" \
  --no-session \
  --mode json \
  --approve \
  "$FULL_PROMPT" \
  2> >("$PI_EVENT_LOGGER" --stderr) \
  | "$PI_EVENT_LOGGER"
PIPE_CODES=("${PIPESTATUS[@]}")
set -e
PI_EXIT=${PIPE_CODES[0]}
LOGGER_EXIT=${PIPE_CODES[1]}

if [[ $PI_EXIT -ne 0 ]]; then
  log "error: pi exited with code $PI_EXIT"
  exit "$PI_EXIT"
fi
if [[ $LOGGER_EXIT -ne 0 ]]; then
  log "error: event logger exited with code $LOGGER_EXIT"
  exit 8
fi

FINAL_STATUS=$(git status --porcelain)
if [[ -z "$FINAL_STATUS" ]]; then
  log "no-op: pi ran successfully, no changes to commit"
  exit 0
fi

log "pi produced changes:"
git status --short >&2

log "running validator: $VALIDATOR"
if ! eval "$VALIDATOR"; then
  log "error: validator failed; aborting commit"
  exit 3
fi

# Stage after validation so validator-generated files are included, then check
# the complete diff (including newly created files) before committing.
git add -A
log "checking staged diff integrity"
# Extra blank lines at EOF are harmless Markdown formatting drift and should not
# discard an otherwise valid curator batch. Disable only blank-at-eof while
# retaining Git's trailing-space and space-before-tab integrity checks.
if ! git -c core.whitespace=-blank-at-eof diff --cached --check; then
  log "error: staged diff check failed; aborting commit"
  exit 3
fi
if git diff --cached --quiet; then
  log "no-op: validator left no changes to commit"
  exit 0
fi

log "committing"
git commit -m "$COMMIT_MESSAGE"

# WIKI_REPO_TOKEN: an HTTPS token for the wiki repo host (GitHub PAT, Gitea /
# GitLab API token, ...).
WIKI_REPO_TOKEN="${WIKI_REPO_TOKEN:-${GITEA_TOKEN:-}}"
if [[ -z "$WIKI_REPO_TOKEN" ]]; then
  log "error: WIKI_REPO_TOKEN is required to push"
  exit 4
fi

PUSH_URL=$(git remote get-url origin)
# Preserve an existing authenticated URL; otherwise inject the masked token for
# HTTPS remotes. Non-HTTPS remotes (including local test remotes) pass through.
if [[ "$PUSH_URL" =~ ^https://[^/]+@ ]]; then
  PUSH_URL_AUTH="$PUSH_URL"
elif [[ "$PUSH_URL" =~ ^https://([^/]+)/(.+)$ ]]; then
  HOST="${BASH_REMATCH[1]}"
  REPO_PATH="${BASH_REMATCH[2]}"
  PUSH_URL_AUTH="https://${WIKI_REPO_TOKEN}@${HOST}/${REPO_PATH}"
else
  PUSH_URL_AUTH="$PUSH_URL"
fi

log "pushing to $GIT_BRANCH"
PUSH_TRIES=0
PUSH_MAX_TRIES=5
while true; do
  PUSH_TRIES=$((PUSH_TRIES + 1))
  git fetch "$PUSH_URL_AUTH" "$GIT_BRANCH"
  if ! git rebase FETCH_HEAD; then
    log "error: rebase onto $GIT_BRANCH failed (try $PUSH_TRIES); aborting"
    git rebase --abort 2>/dev/null || true
    exit 6
  fi
  if git push "$PUSH_URL_AUTH" "HEAD:${GIT_BRANCH}"; then
    break
  fi
  if [[ $PUSH_TRIES -ge $PUSH_MAX_TRIES ]]; then
    log "error: push rejected after $PUSH_MAX_TRIES tries; remote too hot"
    exit 7
  fi
  log "push rejected; retrying (try $PUSH_TRIES/$PUSH_MAX_TRIES)"
done
log "done"
