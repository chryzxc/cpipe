# sh step.sh <run dir> <worktree> <role> <round> <run budget> <limit> <who> [--resume <id>] [--reasoning <effort>]
# Runs one role session from <run dir>/brief-<role>-<round>.md behind header.md (`cpipe.py brief`) under the time limit,
# its output in logs/<role>-<round>.txt, then prints the log's end (the reply and the session id) and the
# run's round counts (`cpipe.py rounds`).
# <who>: hermes -p <profile> chat, or the output of `cpipe.py chat`.
usage() {  # a malformed launch fails here, before any session starts or log is written, and costs no model turn
  echo "step.sh: $1" >&2
  echo "usage: step.sh <run dir> <worktree> <role> <round> <run budget> <limit> <who> [--resume <id>] [--reasoning <effort>]" >&2
  exit 2
}
[ $# -ge 7 ] || usage "needs at least 7 arguments, got $#"
r=$1 w=$2 role=$3 n=$4 budget=$5 limit=$6; shift 6
[ -d "$r" ] || usage "run dir not found: $r"
[ -d "$w" ] || usage "worktree not found: $w"
case $role in ''|*[!a-zA-Z0-9_-]*) usage "role must be a file-safe name: $role" ;; esac
for number in "$n" "$budget" "$limit"; do
  case $number in ''|*[!0-9]*) usage "round, run budget and limit are positive whole numbers, got: $number" ;; esac
  case $number in *[1-9]*) ;; *) usage "round, run budget and limit must be greater than zero" ;; esac
done
profile=default
home=${HERMES_HOME%/}
case "$home" in */profiles/*) profile=${home##*/} ;; esac
prof=
[ "$1" = hermes ] && shift
case $1 in
  -p|--profile)
    [ $# -ge 2 ] || usage "$1 needs a value"
    case $2 in ''|-*) usage "$1 needs a profile name" ;; esac
    prof=$2 profile=$2; shift 2 ;;
esac
[ "$1" = chat ] && shift
validate_options() {
  while [ $# -gt 0 ]; do
    case $1 in -m|--model|--provider|--resume|--reasoning) ;; *) usage "unexpected option: $1" ;; esac
    [ $# -ge 2 ] || usage "$1 needs a value"
    case $2 in ''|-*) usage "$1 needs a value" ;; esac
    shift 2
  done
}
validate_options "$@"
if [ -n "$prof" ]; then set -- -p "$prof" chat "$@"; else set -- chat "$@"; fi
# A resumed session keeps the run budget it started with and refuses the flag, so only a new one gets it
case " $* " in *" --resume "*) again=--resume run= ;; *) again= run="--run-budget $budget" ;; esac
resume=new previous=
for arg in "$@"; do
  [ "$previous" = --resume ] && resume=$arg
  previous=$arg
done
# Only a role with no profile of the user's runs on this chat's model (`cpipe.py chat`, with -m): it gets the stance
case " $* " in *" -m "*|*" --model "*) bare=--bare ;; *) bare= ;; esac
case $role in build) turns=150 ;; plan) turns=120 ;; *) turns=60 ;; esac
log="$r/logs/$role-$n.txt" q="$r/logs/query-$role-$n.md"; mkdir -p "$r/logs"
python3 "$(dirname "$0")/cpipe.py" brief "$r" "$w" "$role" "$n" $again $bare >"$q" 2>"$log" ||
  { echo "exit 2 (no query: see the line above)" >>"$log"; tail -n 30 "$log"; exit 2; }
CPIPE_RUN="$r" CPIPE_STEP="$role" perl "$(dirname "$0")/limit.pl" "$limit" hermes "$@" -Q \
  --query-file "$q" --in "$w" $run --max-turns "$turns" >"$log" 2>&1
status=$?
printf 'cpipe_profile: %s\n' "$profile" >>"$log"
printf 'cpipe_resume: %s\n' "$resume" >>"$log"
echo "exit $status" >>"$log"; tail -n 30 "$log"
python3 "$(dirname "$0")/cpipe.py" rounds "$r"
diagnostic=$?
[ "$status" -ne 0 ] && exit "$status"
exit "$diagnostic"
