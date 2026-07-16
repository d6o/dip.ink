#!/usr/bin/env bash
set -euo pipefail

NOTES_DIR="${NOTES_DIR:-notes}"
N="${INBOX_BATCH_SIZE:-4}"
DEFER="$NOTES_DIR/.deferred"

[[ "$N" =~ ^[1-9][0-9]*$ ]] || { echo "invalid INBOX_BATCH_SIZE: $N" >&2; exit 2; }
mkdir -p "$DEFER"

# Normalize occasional bare note files so the directory-only curator sees them.
for file in "$NOTES_DIR"/*.md; do
  [[ -e "$file" ]] || continue
  base=$(basename "$file" .md)
  [[ "$base" == "README" ]] && continue
  mkdir -p "$NOTES_DIR/$base"
  mv "$file" "$NOTES_DIR/$base/$base.md"
  echo "wrapped bare note: $base.md -> $base/$base.md"
done

pool_file=$(mktemp)
trap 'rm -f "$pool_file"' EXIT
{
  for path in "$NOTES_DIR"/*; do
    [[ -d "$path" ]] || continue
    [[ "$(basename "$path")" == ".deferred" ]] && continue
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
  [[ "$(basename "$path")" == ".deferred" ]] && continue
  live=$((live + 1))
done
for path in "$DEFER"/*; do
  [[ -d "$path" ]] || continue
  held=$((held + 1))
done

echo "inbox pool=$total live=$live held=$held cap=$N"
