# sh lane.sh [-w <seconds>] <lane dir> <repo> <limit> <who> [--resume <id>]
# Runs one delivery of a --parallel batch in its own session, from <lane dir>/brief.md, under the time limit, its
# output appended to <lane dir>/log.txt, then prints the batch's lanes (`cpipe.py lanes`) and this lane's report.
# The session waits on its role steps with the process tool (parallel.md, In a lane); TERMINAL_TIMEOUT caps one
# wait, so it is raised for this session only. -w: wait that long first (a lane that just stopped).
# <who>: -p <profile>, or the output of `cpipe.py chat`. --source cli: Hermes files a one-shot run as `oneshot`,
# which its session lists hide; a lane is the user's to open, so it is filed as a chat.
w=0; if [ "$1" = -w ]; then w=$2; shift 2; fi
d=$1 repo=$2 limit=$3; shift 3
if [ "$1" = -p ]; then prof=$2; shift 2; set -- -p "$prof" chat "$@"; else set -- chat "$@"; fi
q="$d/brief.md"
case " $* " in *" --resume "*) q="$d/continue.md" ;; esac
printf 'Continue this lane (cpipe:run, In a lane in parallel.md): read now.md and go on to the report.\n' >"$d/continue.md"
echo $$ >"$d/pid"  # running from here on, the wait included, so it is never started twice
echo "lane started $(($(date +%s) + w))" >>"$d/log.txt"
sleep "$w"
# One line each time the lane's step changes, for the dispatcher's heartbeat (parallel.md, Start); it ends with
# this script.
issue=$(head -n 1 "$d/brief.md" | sed 's/^# *//')
( last=; while sleep 20 && kill -0 $$ 2>/dev/null; do
    s=$(python3 "$(dirname "$0")/cpipe.py" step "$d")
    [ "$s" = "$last" ] || echo "LANE $(basename "$d") · $issue · $s"; last=$s
  done ) &
watch=$!
CPIPE_LANE="$d" TERMINAL_TIMEOUT="$limit" \
  perl "$(dirname "$0")/limit.pl" "$limit" hermes "$@" -Q --source cli --query-file "$q" --in "$repo" --max-turns 400 \
  >>"$d/log.txt" 2>&1
echo "exit $? $(date +%s)" >>"$d/log.txt"
{ kill "$watch"; wait "$watch"; } 2>/dev/null
python3 "$(dirname "$0")/cpipe.py" lanes "$(dirname "$d")" --report "$(basename "$d")"
