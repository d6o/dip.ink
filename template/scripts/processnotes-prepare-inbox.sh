#!/usr/bin/env bash
set -euo pipefail

NOTES_DIR="${NOTES_DIR:-notes}"
N="${INBOX_BATCH_SIZE:-4}"
DEFER="$NOTES_DIR/.deferred"
BLOCKED="$NOTES_DIR/.blocked"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BLOCK_NOTE_BIN="${BLOCK_NOTE_BIN:-$SCRIPT_DIR/processnotes-block-note.sh}"

[[ "$N" =~ ^[1-9][0-9]*$ ]] || { echo "invalid INBOX_BATCH_SIZE: $N" >&2; exit 2; }
[[ -x "$BLOCK_NOTE_BIN" ]] || { echo "block-note helper is not executable: $BLOCK_NOTE_BIN" >&2; exit 2; }
mkdir -p "$DEFER" "$BLOCKED"

# Normalize occasional bare note files so the directory-only curator sees them.
for file in "$NOTES_DIR"/*.md; do
  [[ -e "$file" ]] || continue
  base=$(basename "$file" .md)
  [[ "$base" == "README" ]] && continue
  mkdir -p "$NOTES_DIR/$base"
  mv "$file" "$NOTES_DIR/$base/$base.md"
  echo "wrapped bare note: $base.md -> $base/$base.md"
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
except (OSError, UnicodeError) as exc:
    print(f"unreadable note: {exc}")
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
except yaml.YAMLError as exc:
    print(f"invalid YAML frontmatter: {str(exc).splitlines()[0]}")
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
    echo "blocking malformed note: $folder ($validation_error)" >&2
    if [[ "$root" == "$DEFER" ]]; then
      [[ ! -e "$NOTES_DIR/$folder" ]] || { echo "cannot restore malformed deferred note; live path exists: $folder" >&2; exit 3; }
      mv "$path" "$NOTES_DIR/$folder"
    fi
    NOTES_DIR="$NOTES_DIR" "$BLOCK_NOTE_BIN" "$folder" malformed-note
  done
done

pool_file=$(mktemp)
trap 'rm -f "$pool_file"' EXIT
{
  for path in "$NOTES_DIR"/*; do
    [[ -d "$path" ]] || continue
    case "$(basename "$path")" in
      .deferred|.blocked) continue ;;
    esac
    basename "$path"
  done
  for path in "$DEFER"/*; do
    [[ -d "$path" ]] || continue
    basename "$path"
  done
} | sort -u > "$pool_file"

total=$(wc -l < "$pool_file" | tr -d ' ')
index=0
while IFS= read -r folder; do
  [[ -n "$folder" ]] || continue
  index=$((index + 1))
  if [[ "$index" -le "$N" ]]; then
    if [[ -d "$DEFER/$folder" && ! -d "$NOTES_DIR/$folder" ]]; then
      mv "$DEFER/$folder" "$NOTES_DIR/$folder"
    fi
  elif [[ -d "$NOTES_DIR/$folder" && ! -d "$DEFER/$folder" ]]; then
    mv "$NOTES_DIR/$folder" "$DEFER/$folder"
  fi
done < "$pool_file"

live=0
held=0
for path in "$NOTES_DIR"/*; do
  [[ -d "$path" ]] || continue
  case "$(basename "$path")" in
    .deferred|.blocked) continue ;;
  esac
  live=$((live + 1))
done
for path in "$DEFER"/*; do
  [[ -d "$path" ]] || continue
  held=$((held + 1))
done
blocked=0
for path in "$BLOCKED"/*; do
  [[ -d "$path" ]] || continue
  blocked=$((blocked + 1))
done

echo "inbox pool=$total live=$live held=$held blocked=$blocked cap=$N"
