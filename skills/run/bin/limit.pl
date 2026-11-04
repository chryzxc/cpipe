# perl limit.pl [-h] <seconds> <command> [<arguments>]: run a command at low priority under a time limit (exit 124).
# Low priority is nice 10, plus on macOS the utility QoS clamp (nice alone does not keep it off the fast cores);
# every process the command starts inherits both.
# -h: a heavy job; heavy jobs from every run on the machine take turns, and the limit starts once its turn comes.
use Fcntl ':flock'; my $l;
if ($ARGV[0] eq '-h') { shift; open $l, '>>', '/tmp/cpipe-heavy.lock' or die "lock: $!"; flock $l, LOCK_EX }
my $t = shift; my $p = fork // die "fork: $!";
if (!$p) { setpgrp; setpriority 0, 0, 10;
  my @q = -x "/usr/sbin/taskpolicy" ? ("/usr/sbin/taskpolicy", "-c", "utility") : (); exec @q, @ARGV or exit 127 }
$SIG{ALRM} = $SIG{TERM} = $SIG{INT} = sub { kill 'TERM', -$p; sleep 5; kill 'KILL', -$p; exit 124 };
alarm $t; waitpid $p, 0; exit($? >> 8 or $? & 127);
