#!/bin/bash
# Uninstall the software-delivery workflow from the Hermes user-state layer.
# Leaves roster.yaml, kanban.db, and all profile data untouched.
#   --purge   also remove ~/.hermes/roster.yaml (asks once unless --yes)
#   --yes     run without confirmation prompts
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
H="${HERMES_HOME:-$HOME/.hermes}"
PURGE=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    --yes) ASSUME_YES=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 1 ;;
  esac
done

echo "== software-delivery uninstall =="
echo "repo: $REPO"

confirm() {
  [ "$ASSUME_YES" = "1" ] && return 0
  read -r -p "$1 [y/N] " ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ]
}

echo "-- 1/5 cron jobs (owned by software-delivery)"
python3 - "$REPO/workflow/cron.jobs.json" "$H/cron/jobs.json" <<'PY'
import json, sys
defs_path, live_path = sys.argv[1], sys.argv[2]
try:
    live = json.load(open(live_path))
except FileNotFoundError:
    raise SystemExit(0)
jobs = live if isinstance(live, list) else live.get('jobs', [])
owned_names = {d['name'] for d in json.load(open(defs_path))}
kept = [j for j in jobs if not (
    j.get('owner') == 'software-delivery' or j.get('name') in owned_names)]
removed = len(jobs) - len(kept)
if isinstance(live, dict):
    live['jobs'] = kept
else:
    live = kept
json.dump(live, open(live_path, 'w'), indent=2, ensure_ascii=False)
print(f"   removed {removed} owned job(s)")
PY

echo "-- 2/5 deployed script copies"
if [ -d "$H/scripts" ]; then
  for f in "$REPO"/workflow/scripts/*; do
    name="$(basename "$f")"
    if [ -f "$H/scripts/$name" ] && cmp -s "$H/scripts/$name" "$f"; then
      rm -f "$H/scripts/$name"
      echo "   removed $name"
    fi
  done
fi

echo "-- 3/5 skill symlinks (global + profiles, only links into this repo)"
find "$H/skills" -maxdepth 1 -type l -name "my-*" 2>/dev/null | while read -r link; do
  [ "$(readlink "$link")" = "$REPO/workflow/skills/$(basename "$link")" ] && rm -f "$link" && echo "   removed $link"
done
find "$H/profiles" -maxdepth 3 -type l -name "my-*" 2>/dev/null | while read -r link; do
  [ "$(readlink "$link")" = "$REPO/workflow/skills/$(basename "$link")" ] && rm -f "$link"
done

echo "-- 4/5 plugin placement"
if [ "$(readlink "$H/plugins/software-delivery" 2>/dev/null)" = "$REPO" ]; then
  rm -f "$H/plugins/software-delivery"
  echo "   removed plugin link"
fi
HERMES_BIN="$(command -v hermes || true)"
[ -n "$HERMES_BIN" ] || HERMES_BIN="$H/hermes-agent/venv/bin/hermes"
if [ -x "$HERMES_BIN" ]; then
  "$HERMES_BIN" plugins disable software-delivery </dev/null >/dev/null 2>&1 || true
fi

echo "-- 5/5 roster"
if [ "$PURGE" = "1" ]; then
  if confirm "Also remove $H/roster.yaml?"; then
    rm -f "$H/roster.yaml"
    echo "   roster removed"
  fi
else
  echo "   kept (use --purge to remove)"
fi

echo "== uninstall complete (repositories and kanban.db untouched) =="
