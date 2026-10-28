# sh lane.sh [-w <seconds>] <lane dir> <repo> <limit> <who> [--resume <id>]
# Runs one delivery of a --parallel batch in its own session, from <lane dir>/brief.md, under the time limit, its
# output appended to <lane dir>/log.txt, then prints the batch's lanes (`cpipe.py lanes`) and this lane's report.
# The session waits on its role steps in the foreground: like Hermes' kanban dispatcher does for a long worker, the
# terminal's timeouts are raised for this session only. -w: wait that long first (a lane that just stopped).
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
CPIPE_LANE="$d" TERMINAL_TIMEOUT="$limit" TERMINAL_MAX_FOREGROUND_TIMEOUT="$limit" \
  perl "$(dirname "$0")/limit.pl" "$limit" hermes "$@" -Q --source cli --query-file "$q" --in "$repo" --max-turns 400 \
  >>"$d/log.txt" 2>&1
echo "exit $? $(date +%s)" >>"$d/log.txt"
python3 "$(dirname "$0")/cpipe.py" lanes "$(dirname "$d")" --report "$(basename "$d")"
