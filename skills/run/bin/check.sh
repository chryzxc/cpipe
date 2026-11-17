# sh check.sh [-h] <seconds> <log> <command> [<arguments>]: run one check under the time limit (`limit.pl`), its whole
# output and an `exit <code>` line in <log>, then print the log's end. It exits with the check's own status, so no
# pipe (`| tail`, `| grep`) stands between the check and the code that says whether it passed.
heavy=
[ "$1" = -h ] && { heavy=-h; shift; }
if [ $# -lt 3 ]; then
  echo "usage: check.sh [-h] <seconds> <log> <command> [<arguments>]" >&2
  exit 2
fi
seconds=$1 log=$2; shift 2
case $seconds in ''|*[!0-9]*) echo "check.sh: seconds must be a positive whole number" >&2; exit 2 ;; esac
case $seconds in *[1-9]*) ;; *) echo "check.sh: seconds must be greater than zero" >&2; exit 2 ;; esac
mkdir -p "$(dirname "$log")" || exit 2
: >"$log" || exit 2
perl "$(dirname "$0")/limit.pl" $heavy "$seconds" "$@" >"$log" 2>&1
status=$?
echo "exit $status" >>"$log" || exit 2
tail -n 30 "$log"
exit "$status"
