# sh tick.sh <run dir> <worktree>: wait 10 minutes, then print what changed
sleep 600; r=$1; cd "$2" || exit 0
date +%H:%M; git diff --shortstat
echo "$(git ls-files --others --exclude-standard | wc -l | tr -d ' ') new files"
ls -t "$r/logs" | head -3 | while read -r f; do
  [ -f "$r/logs/$f" ] || continue
  echo "$f $(date -r "$r/logs/$f" +%H:%M): $(grep -v '^[[:space:]]*$' "$r/logs/$f" | tail -1 | cut -c1-120)"
done
