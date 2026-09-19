#!/bin/bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
H="${HERMES_HOME:-$HOME/.hermes}"
SKILLS_SRC="$REPO/workflow/skills"
SCRIPTS_SRC="$REPO/workflow/scripts"
HERMES_BIN="$(command -v hermes || true)"
[ -n "$HERMES_BIN" ] || HERMES_BIN="$H/hermes-agent/venv/bin/hermes"

echo "== delivery-workflow install =="
echo "repo: $REPO"

echo "-- 1/8 plugin placement"
mkdir -p "$H/plugins"
ln -sfn "$REPO" "$H/plugins/delivery-workflow"

echo "-- 2/8 roster (role -> profile mapping)"
python3 "$SCRIPTS_SRC/setup_roster.py" "$@" || echo "   roster not configured yet — rerun: ./install.sh --roster coordinator=<profile> implementer=<profile> reviewer=<profile> verifier=<profile> security_reviewer=<profile>"

echo "-- 3/8 global skills (symlinks)"
for dir in "$SKILLS_SRC"/*/; do
  name="$(basename "$dir")"
  case "$name" in my-*) ;; *) continue ;; esac
  rm -rf "$H/skills/$name"
  ln -sfn "$SKILLS_SRC/$name" "$H/skills/$name"
done

echo "-- 4/8 profile skill fan-out (symlinks)"
count=0
while IFS= read -r d; do
  name="$(basename "$d")"
  [ -d "$SKILLS_SRC/$name" ] || continue
  rm -rf "$d"
  ln -sfn "$SKILLS_SRC/$name" "$d"
  count=$((count + 1))
done < <(find "$H/profiles" -maxdepth 3 -name "my-*" \( -type d -o -type l \) 2>/dev/null)
echo "   profile links: $count"

echo "-- 5/8 scripts (copies; cron requires resolution inside $H/scripts)"
mkdir -p "$H/scripts"
for f in "$SCRIPTS_SRC"/*; do
  [ -f "$f" ] || continue
  name="$(basename "$f")"
  case "$name" in
    setup_roster.py|merge_cron_jobs.py|assert_config.py) continue ;;
  esac
  cp "$f" "$H/scripts/$name"
  chmod +x "$H/scripts/$name"
done

echo "-- 6/8 cron job definitions"
python3 "$SCRIPTS_SRC/merge_cron_jobs.py" "$REPO/workflow/cron.jobs.json" "$H/cron/jobs.json"

echo "-- 7/8 config assertions"
python3 "$SCRIPTS_SRC/assert_config.py" "$REPO/workflow/config.assertions.yaml" "$H/config.yaml"

echo "-- 8/8 enable plugin"
if [ -x "$HERMES_BIN" ]; then
  "$HERMES_BIN" plugins enable delivery-workflow </dev/null >/dev/null 2>&1 \
    && echo "   plugin enabled (takes effect on next session)" \
    || echo "   could not auto-enable — run: hermes plugins enable delivery-workflow"
else
  echo "   hermes binary not found — run: hermes plugins enable delivery-workflow"
fi

python3 "$H/scripts/check_delivery_config.py" || true
echo "== install complete =="
