#!/bin/bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
H="${HERMES_HOME:-$HOME/.hermes}"
SKILLS_SRC="$REPO/workflow/skills"
SCRIPTS_SRC="$REPO/workflow/scripts"

echo "== delivery-workflow install =="
echo "repo: $REPO"

echo "-- 1/6 plugin placement"
mkdir -p "$H/plugins"
ln -sfn "$REPO" "$H/plugins/delivery-workflow"

if [ -f "$REPO/workflow/roster.yaml" ]; then
  cp "$REPO/workflow/roster.yaml" "$H/roster.yaml"
elif [ ! -f "$H/roster.yaml" ]; then
  cp "$REPO/workflow/roster.example.yaml" "$H/roster.yaml"
  echo "   NOTE: created ~/.hermes/roster.yaml from the example — edit it to map roles to YOUR profiles"
fi

echo "-- 2/6 global skills (symlinks)"
for dir in "$SKILLS_SRC"/*/; do
  name="$(basename "$dir")"
  case "$name" in my-*) ;; *) continue ;; esac
  rm -rf "$H/skills/$name"
  ln -sfn "$SKILLS_SRC/$name" "$H/skills/$name"
done

echo "-- 3/6 profile skill fan-out (symlinks)"
count=0
while IFS= read -r d; do
  name="$(basename "$d")"
  [ -d "$SKILLS_SRC/$name" ] || continue
  rm -rf "$d"
  ln -sfn "$SKILLS_SRC/$name" "$d"
  count=$((count + 1))
done < <(find "$H/profiles" -maxdepth 3 -name "my-*" \( -type d -o -type l \) 2>/dev/null)
echo "   profile links: $count"

echo "-- 4/6 scripts (copies; cron requires resolution inside $H/scripts)"
mkdir -p "$H/scripts"
for f in "$SCRIPTS_SRC"/*.py; do
  cp "$f" "$H/scripts/$(basename "$f")"
  chmod +x "$H/scripts/$(basename "$f")"
done

echo "-- 5/6 cron job definitions"
python3 - "$REPO/workflow/cron.jobs.json" "$H/cron/jobs.json" << 'PYEOF'
import json, sys

defs_path, live_path = sys.argv[1], sys.argv[2]
defs = json.load(open(defs_path))
live = json.load(open(live_path))
jobs = live if isinstance(live, list) else live.setdefault('jobs', live.get('jobs', []))
OWNED = {'prompt', 'script', 'no_agent', 'monitor_script', 'model', 'provider',
         'schedule', 'enabled', 'deliver'}
existing = {j.get('name'): j for j in jobs}
for d in defs:
    j = existing.get(d['name'])
    if j is None:
        jobs.append(dict(d))
        print(f"   added: {d['name']}")
    else:
        changed = [k for k in OWNED if k in d and j.get(k) != d[k]]
        for k in changed:
            j[k] = d[k]
        print(f"   updated: {d['name']}" + (f" ({', '.join(changed)})" if changed else " (in sync)"))
json.dump(live, open(live_path, 'w'), indent=2, ensure_ascii=False)
PYEOF

echo "-- 6/6 config assertions + policy check"
python3 - "$REPO/workflow/config.assertions.yaml" "$H/config.yaml" << 'PYEOF'
import re, sys, yaml

expected = yaml.safe_load(open(sys.argv[1]))
text = open(sys.argv[2]).read()
drift = []
for key, want in expected.get('kanban', {}).items():
    m = re.search(rf'^\s*{key}:\s*(\d+)\s*$', text, re.M)
    got = int(m.group(1)) if m else None
    if got != want:
        drift.append(f"kanban.{key}: expected {want}, found {got}")
if drift:
    print("CONFIG DRIFT:\n" + "\n".join('  - ' + d for d in drift))
    sys.exit(1)
print("config assertions OK")
PYEOF

python3 "$H/scripts/check_delivery_config.py" || true
echo "== install complete =="
