#!/bin/bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
H="${HERMES_HOME:-$HOME/.hermes}"
SKILLS_SRC="$REPO/workflow/skills"
SCRIPTS_SRC="$REPO/workflow/scripts"
HERMES_BIN="$(command -v hermes || true)"
[ -n "$HERMES_BIN" ] || HERMES_BIN="$H/hermes-agent/venv/bin/hermes"

echo "== software-delivery install =="
echo "repo: $REPO"

echo "-- 1/9 plugin placement"
mkdir -p "$H/plugins"
rm -f "$H/plugins/delivery-workflow"
ln -sfn "$REPO" "$H/plugins/software-delivery"

echo "-- 2/9 roster (role -> profile mapping)"
python3 "$SCRIPTS_SRC/setup_roster.py" "$@" || echo "   roster not configured yet — rerun: ./install.sh --roster coordinator=<profile> implementer=<profile> reviewer=<profile> verifier=<profile> security_reviewer=<profile>"

echo "-- 3/9 global skills (symlinks)"
for dir in "$SKILLS_SRC"/*/; do
  name="$(basename "$dir")"
  case "$name" in my-*) ;; *) continue ;; esac
  rm -rf "$H/skills/$name"
  ln -sfn "$SKILLS_SRC/$name" "$H/skills/$name"
done

echo "-- 4/9 profile skill fan-out (symlinks)"
count=0
while IFS= read -r d; do
  name="$(basename "$d")"
  [ -d "$SKILLS_SRC/$name" ] || continue
  rm -rf "$d"
  ln -sfn "$SKILLS_SRC/$name" "$d"
  count=$((count + 1))
done < <(find "$H/profiles" -maxdepth 3 -name "my-*" \( -type d -o -type l \) 2>/dev/null)
echo "   profile links: $count"

echo "-- 5/9 scripts (copies; cron requires resolution inside $H/scripts)"
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

echo "-- 6/9 example bots (copies into roster-mapped profiles; SKIP_BOTS=1 to keep your own)"
if [ "${SKIP_BOTS:-0}" = "1" ]; then
  echo "   skipped (SKIP_BOTS=1)"
else
  stamp="$(date +%Y%m%d%H%M%S)"
  for dir in "$REPO"/workflow/bots/*/; do
    role="$(basename "$dir")"
    profile="$(python3 "$SCRIPTS_SRC/resolve_role.py" "$role" 2>/dev/null)" || { echo "   $role: no profile mapped, skipped"; continue; }
    dest="$H/profiles/$profile"
    [ -d "$dest" ] || { echo "   $role: profile '$profile' missing, skipped"; continue; }
    while IFS= read -r f; do
      rel="${f#$dir}"
      if [ -f "$dest/$rel" ] && ! cmp -s "$f" "$dest/$rel"; then
        cp "$dest/$rel" "$dest/$rel.bak-install-$stamp"
      fi
      mkdir -p "$(dirname "$dest/$rel")"
      cp "$f" "$dest/$rel"
    done < <(find "$dir" -type f ! -name .DS_Store)
    echo "   $role -> $profile"
  done
fi

echo "-- 7/9 cron job definitions"
python3 "$SCRIPTS_SRC/merge_cron_jobs.py" "$REPO/workflow/cron.jobs.json" "$H/cron/jobs.json"

echo "-- 8/9 config assertions"
python3 "$SCRIPTS_SRC/assert_config.py" "$REPO/workflow/config.assertions.yaml" "$H/config.yaml"

echo "-- 9/9 enable plugin"
if [ -x "$HERMES_BIN" ]; then
  "$HERMES_BIN" plugins enable software-delivery </dev/null >/dev/null 2>&1 \
    && echo "   plugin enabled (takes effect on next session)" \
    || echo "   could not auto-enable — run: hermes plugins enable software-delivery"
else
  echo "   hermes binary not found — run: hermes plugins enable software-delivery"
fi

if [ -x "$HERMES_BIN" ]; then
  "$HERMES_BIN" plugins disable delivery-workflow </dev/null >/dev/null 2>&1 || true
fi

python3 "$H/scripts/check_delivery_config.py" || true
echo "== install complete =="
