#!/usr/bin/env bash
# Beacon nightly scan — deterministic, no LLM. Output is injected into the
# Beacon cron agent's prompt for triage. Every section is guarded; a failed
# check prints CHECK_FAILED and exits 0 so the agent can file a BLOCKED card.

set -u
CONF="${HOME}/.hermes/scripts/beacon-repos.conf"
BIG_FILE_KB=${BIG_FILE_KB:-1024}
MAX_ASSETS=${MAX_ASSETS:-15}
OUTDATED_TIMEOUT=${OUTDATED_TIMEOUT:-20}

section() { printf '\n===== %s =====\n' "$1"; }

if [ ! -s "$CONF" ]; then
  section "CONFIG"
  echo "CHECK_FAILED: no repos configured in $CONF (one absolute repo path per line)"
  exit 0
fi

total_new=0

while IFS= read -r repo || [ -n "$repo" ]; do
  case "$repo" in ''|\#*) continue ;; esac
  [ -d "$repo" ] || { section "REPO ${repo}"; echo "CHECK_FAILED: path missing"; continue; }

  section "REPO $(basename "$repo")"

  # --- git snapshot ---
  if git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "head: $(git -C "$repo" rev-parse --short HEAD 2>/dev/null)"
    echo "branch: $(git -C "$repo" branch --show-current 2>/dev/null)"
    dirty=$(git -C "$repo" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    echo "dirty_files: ${dirty}"
    echo "last_commit: $(git -C "$repo" log -1 --format='%cs %s' 2>/dev/null | cut -c1-80)"
  else
    echo "CHECK_FAILED: not a git work tree"
  fi

  # --- oversized assets (deterministic weight signal) ---
  section "ASSET WEIGHT $(basename "$repo") (>${BIG_FILE_KB}KB, top ${MAX_ASSETS})"
  big=$(find "$repo" -type f -size +"${BIG_FILE_KB}k" \
    -not -path "*/.git/*" -not -path "*/node_modules/*" -not -path "*/.build/*" \
    -not -path "*/venv/*" -not -path "*/.venv/*" -not -name "*.lock" \
    -exec du -k {} + 2>/dev/null | sort -rn | head -"$MAX_ASSETS")
  if [ -n "$big" ]; then echo "$big"; else echo "NO FINDINGS"; fi

  # --- uncompressed raster images (optimization-audit static item) ---
  section "RASTER IMAGES $(basename "$repo") (top ${MAX_ASSETS} by size)"
  imgs=$(find "$repo" -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.tiff' \) \
    -not -path "*/.git/*" -not -path "*/node_modules/*" -not -path "*/.build/*" \
    -exec du -k {} + 2>/dev/null | sort -rn | head -"$MAX_ASSETS")
  if [ -n "$imgs" ]; then echo "$imgs"; else echo "NO FINDINGS"; fi

  # --- dependency manifests present (staleness needs per-ecosystem tooling) ---
  section "MANIFESTS $(basename "$repo")"
  for m in package.json pyproject.toml requirements.txt Podfile Package.swift go.mod Cargo.toml; do
    [ -f "$repo/$m" ] && echo "present: $m"
  done
  echo "(manifest-only signal; run ecosystem outdated check when triggered)"

  # --- best-effort npm outdated (bounded, optional) ---
  if [ -f "$repo/package.json" ] && command -v npm >/dev/null 2>&1; then
    section "NPM OUTDATED $(basename "$repo") (bounded ${OUTDATED_TIMEOUT}s)"
    if rows=$(cd "$repo" && timeout "$OUTDATED_TIMEOUT" npm outdated 2>/dev/null | head -20) && [ -n "$rows" ]; then
      echo "$rows"
    else
      echo "NO FINDINGS (or check timed out / unavailable)"
    fi
  fi

  # --- debt markers (trend signal only; not findings by themselves) ---
  section "DEBT MARKERS $(basename "$repo")"
  markers=$(grep -rIn --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.build \
    -E 'TODO|FIXME|HACK|XXX' "$repo" 2>/dev/null | wc -l | tr -d ' ')
  echo "todo_fixme_count: ${markers}"

done < "$CONF"

section "SUMMARY"
echo "scan_completed_utc: $(date -u +%FT%TZ)"
echo "raw signal only — Beacon triage classifies findings; empty sections are NO FINDINGS"
