# sh wait.sh <run dir> <seconds> <log>...
# Waits in the foreground until every role step started in the background (`step.sh`) has written its exit line to
# its log, then prints each log's end and the run's round counts (`cpipe.py rounds`), as `step.sh` does. After
# <seconds> it returns with `still running: <log>` instead, before Hermes' cap on one tool call cuts it off
# (parallel.md, In a lane): a lane spends one call per <seconds>, where the process tool's wait is cut at the
# terminal timeout of the user's config.
r=$1 end=$(($(date +%s) + $2)); shift 2
for log in "$@"; do
  until tail -n 1 "$log" 2>/dev/null | grep -Eq '^exit [0-9]+'; do
    [ "$(date +%s)" -ge "$end" ] && { echo "still running: $log"; exit 3; }
    sleep 5
  done
done
for log in "$@"; do echo "== $log"; tail -n 30 "$log"; done
python3 "$(dirname "$0")/cpipe.py" rounds "$r"
