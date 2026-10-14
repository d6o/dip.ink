#!/usr/bin/env bash
set -euo pipefail
# Use only synthetic inputs and a disposable local origin.
[[ $(id -u) == 0 ]] || { echo 'The root fixture requires UID 0.' >&2; exit 2; }
TMP=$(mktemp -d)
chmod 755 "$TMP"
trap 'rm -rf "$TMP"' EXIT
REPO="$TMP/repo"; ORIGIN="$TMP/origin"
git init -q --bare "$ORIGIN"
git clone -q "$ORIGIN" "$REPO"
git -C "$REPO" switch -q -c main
git -C "$REPO" config user.name fixture
git -C "$REPO" config user.email fixture@example.invalid
mkdir -p "$REPO/notes" "$REPO/wiki"
printf 'original\n' > "$REPO/wiki/page.md"
printf 'Process only the prepared synthetic notes.\n' > "$REPO/task.md"
for i in $(seq 1 13); do
 slug=$(printf '2026-01-01-0000%02d-fixture' "$i")
 mkdir -p "$REPO/notes/$slug"
 cat > "$REPO/notes/$slug/NOTE.md" <<'NOTE'
---
captured: 2026-01-01T00:00:00Z
session: disposable root fixture
topic: prepared boundary
---

# Fixture

Synthetic root canary source.
NOTE
done
git -C "$REPO" add .; git -C "$REPO" commit -qm 'Add synthetic root inputs'; git -C "$REPO" push -qu origin main
chown -R 1001:1001 "$ORIGIN"
cat > "$TMP/pi" <<'PI'
#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) == 1001 ]]
[[ -r "$INBOX_PREPARED_STATE" ]]
[[ $(stat -c %a "$INBOX_PREPARED_STATE") == 600 ]]
[[ $(stat -c %a "$(dirname "$INBOX_PREPARED_STATE")") == 700 ]]
python3 "$INBOX_GUARD_BIN" verify --state "$INBOX_PREPARED_STATE" --sha256 "$INBOX_PREPARED_SHA256" --repo "$PWD" >/dev/null
find notes -mindepth 1 -maxdepth 1 -type d ! -name .deferred ! -name .blocked -exec rm -rf -- {} +
printf 'changed\n' > wiki/page.md
printf '{}\n'
PI
cat > "$TMP/logger" <<'LOGGER'
#!/usr/bin/env bash
cat
LOGGER
cat > "$TMP/now" <<'NOW'
#!/usr/bin/env bash
count=$(cat "$FIXTURE_NOW_COUNT" 2>/dev/null || echo 0)
printf '%s\n' "$((count+1))" > "$FIXTURE_NOW_COUNT"
if [[ $count == 0 ]]; then echo 0; else echo 3000; fi
NOW
chmod 755 "$TMP/pi" "$TMP/logger" "$TMP/now"
export FIXTURE_NOW_COUNT="$TMP/now-count"
cd "$REPO"
PROMPT_PATH="$REPO/task.md" PI_API_KEY=synthetic-unused WIKI_REPO_TOKEN=synthetic-unused \
 PI_BIN="$TMP/pi" PI_EVENT_LOGGER="$TMP/logger" VALIDATOR=true COMMIT_MESSAGE='Record root boundary fixture' \
 CURATOR_PREFLIGHT=0 PI_PROVIDER=openai PI_MODEL=synthetic-unused SUPERVISOR_START_EPOCH=0 \
 SUPERVISOR_NOW_BIN="$TMP/now" GITHUB_ENV="$TMP/summary" \
 bash /opt/dip.ink/scripts/processnotes-supervisor.sh
[[ $(git rev-list --count HEAD) == 2 ]]
[[ $(git --git-dir="$ORIGIN" rev-list --count main) == 2 ]]
[[ $(find notes/.deferred -mindepth 1 -maxdepth 1 -type d | wc -l) == 9 ]]
[[ $(find notes -mindepth 1 -maxdepth 1 -type d ! -name .deferred ! -name .blocked | wc -l) == 0 ]]
grep -q '^BATCHES_COMPLETED=1$' "$TMP/summary"
echo 'root preparation -> uid1001 guard -> commit: passed'
