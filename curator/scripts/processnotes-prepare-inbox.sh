#!/usr/bin/env bash
set -euo pipefail

NOTES_DIR="${NOTES_DIR:-notes}"
N="${INBOX_BATCH_SIZE:-4}"
DEFER="$NOTES_DIR/.deferred"
BLOCKED="$NOTES_DIR/.blocked"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BLOCK_NOTE_BIN="${BLOCK_NOTE_BIN:-$SCRIPT_DIR/processnotes-block-note.sh}"
GUARD_BIN="$SCRIPT_DIR/processnotes-batch-guard.py"

[[ "$N" =~ ^[1-9][0-9]*$ ]] || { echo "invalid INBOX_BATCH_SIZE" >&2; exit 2; }
[[ -x "$BLOCK_NOTE_BIN" ]] || { echo "block-note helper is not executable" >&2; exit 2; }
# Ambiguous roots must fail before any folder move or note change.
python3 "$GUARD_BIN" preflight --notes-dir "$NOTES_DIR" >/dev/null
mkdir -p "$DEFER" "$BLOCKED"

# Normalize occasional bare note files so the directory-only curator sees them.
for file in "$NOTES_DIR"/*.md; do
  [[ -e "$file" ]] || continue
  base=$(basename "$file" .md)
  [[ "$base" == "README" ]] && continue
  mkdir -p "$NOTES_DIR/$base"
  mv "$file" "$NOTES_DIR/$base/$base.md"
  echo "wrapped bare note"
done

validate_note_folder() {
  local path=$1 folder=$2 source_file=""
  if [[ -f "$path/$folder.md" ]]; then
    source_file="$path/$folder.md"
  elif [[ -f "$path/NOTE.md" ]]; then
    source_file="$path/NOTE.md"
  else
    echo "missing canonical note file ($folder.md or NOTE.md)"
    return 1
  fi

  python3 - "$source_file" <<'PY'
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1])
try:
    text = path.read_text(encoding="utf-8")
except (OSError, UnicodeError):
    print("unreadable-note")
    raise SystemExit(1)

lines = text.splitlines()
if not lines or lines[0].strip() != "---":
    print("missing opening YAML frontmatter delimiter")
    raise SystemExit(1)
try:
    end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
except StopIteration:
    print("missing closing YAML frontmatter delimiter")
    raise SystemExit(1)

try:
    frontmatter = yaml.safe_load("\n".join(lines[1:end]))
except yaml.YAMLError:
    print("invalid-YAML-frontmatter")
    raise SystemExit(1)
if not isinstance(frontmatter, dict):
    print("YAML frontmatter must be a mapping")
    raise SystemExit(1)
for key in ("captured", "session", "topic"):
    if key not in frontmatter or frontmatter[key] is None or str(frontmatter[key]).strip() == "":
        print(f"missing required frontmatter field: {key}")
        raise SystemExit(1)
PY
}

# Terminally quarantine malformed live or deferred folders before selecting a
# batch. This is deliberately provider-free: one bad note cannot consume an LLM
# call or poison otherwise valid neighbors in the oldest-first queue.
for root in "$NOTES_DIR" "$DEFER"; do
  for path in "$root"/*; do
    [[ -d "$path" ]] || continue
    folder=$(basename "$path")
    case "$folder" in
      .deferred|.blocked) continue ;;
    esac
    if validation_error=$(validate_note_folder "$path" "$folder" 2>&1); then
      continue
    fi
    echo "blocking malformed note" >&2
    if [[ "$root" == "$DEFER" ]]; then
      [[ ! -e "$NOTES_DIR/$folder" ]] || { echo "cannot restore malformed deferred note; live path exists" >&2; exit 3; }
      mv "$path" "$NOTES_DIR/$folder"
    fi
    # The helper can print paths. Keep its output outside the provider log.
    if ! NOTES_DIR="$NOTES_DIR" "$BLOCK_NOTE_BIN" "$folder" malformed-note >/dev/null 2>&1; then
      echo "malformed note block failed" >&2
      exit 3
    fi
  done
done

# Select exact identities, not a deduplicated list of names. The guard checks
# the complete folder metadata after the queue transitions.
python3 "$GUARD_BIN" prepare --notes-dir "$NOTES_DIR" --cap "$N"
