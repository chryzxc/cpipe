# sh step.sh <run dir> <worktree> <role> <round> <run budget> <limit> <who> [--resume <id>] [--reasoning <effort>]
# Runs one role session from <run dir>/brief-<role>-<round>.md behind header.md (`cpipe.py brief`) under the time limit,
# its output in logs/<role>-<round>.txt, then prints the log's end (the reply and the session id).
# <who>: hermes -p <profile> chat, or the output of `cpipe.py chat`.
r=$1 w=$2 role=$3 n=$4 budget=$5 limit=$6; shift 6
if [ "$1" = hermes ]; then shift  # written out, so the chat's UI shows the handoff
elif [ "$1" = -p ]; then prof=$2; shift 2; set -- -p "$prof" chat "$@"; else set -- chat "$@"; fi
# A resumed session keeps the run budget it started with and refuses the flag, so only a new one gets it
case " $* " in *" --resume "*) again=--resume run= ;; *) again= run="--run-budget $budget" ;; esac
case $role in build) turns=150 ;; plan) turns=120 ;; *) turns=60 ;; esac
log="$r/logs/$role-$n.txt" q="$r/logs/query-$role-$n.md"; mkdir -p "$r/logs"
python3 "$(dirname "$0")/cpipe.py" brief "$r" "$w" "$role" "$n" $again >"$q" 2>"$log" ||
  { echo "exit 2 (no query: see the line above)" >>"$log"; tail -n 30 "$log"; exit 2; }
CPIPE_RUN="$r" CPIPE_STEP="$role" perl "$(dirname "$0")/limit.pl" "$limit" hermes "$@" -Q \
  --query-file "$q" --in "$w" $run --max-turns "$turns" >"$log" 2>&1
echo "exit $?" >>"$log"; tail -n 30 "$log"
