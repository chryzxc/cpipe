"""Local delivery diagnostics."""
import argparse
import json
import sys


def setup_parser(parser):
    sub = parser.add_subparsers(dest='wr_command')
    sub.add_parser('doctor', help='check plugin registration and native host interfaces')
    sub.add_parser('status', help='inspect profile-local deliveries (read-only)')
    sub.add_parser('smoke', help='run the offline fixture, not a production delivery')
    live = sub.add_parser('live-probe', help='explicit unsandboxed live-model fixture; consumes model usage')
    live.add_argument('--builder', required=True)
    live.add_argument('--reviewer', required=True)
    live.add_argument('--seconds', type=int, default=480)


def handle(args):
    command = getattr(args, 'wr_command', None) or 'doctor'
    try:
        if command == 'doctor':
            from .. import __version__
            from ..delivery.native_bridge import NativeBridge
            from ..delivery.approval import arguments
            from hermes_cli.plugins import PluginContext
            from hermes_cli import kanban_db
            ready = all(callable(getattr(PluginContext, name, None)) for name in
                        ('register_tool', 'register_command', 'register_cli_command', 'inject_message'))
            ready = ready and callable(getattr(kanban_db, 'claim_task', None))
            warnings = []
            try:  # cards cpipe owns must stay with cpipe; anything else taking them stalls runs
                from contextlib import closing
                from . import hermes_api as h
                with closing(h.connect(None)) as conn:
                    taken = conn.execute("SELECT count(*) FROM tasks WHERE created_by='cpipe' AND status!='archived' "
                        "AND status='running' AND claim_lock NOT LIKE '%:cpipe-%'"
                        ).fetchone()[0]
                if taken:
                    warnings.append(f"{taken} cpipe card(s) were claimed by something else, such as an "
                                    'older cpipe background service or a board automation; stop it, or exclude '
                                    "cards created by cpipe from it")
            except Exception as exc:
                warnings.append('could not inspect the board: ' + type(exc).__name__)
            print(json.dumps(dict(version=__version__, native_interfaces=ready,
                completion_target='local_only', sandboxed=False, warnings=warnings)))
            return 0 if ready else 1
        if command == 'status':
            from hermes_constants import get_hermes_home
            from ..delivery.journal import Journal
            from ..delivery.service import State
            from ..delivery.controller import summary
            path = get_hermes_home() / 'cpipe-deliveries' / 'facts.db'
            if not path.exists():
                print(json.dumps(dict(deliveries=[])))
                return 0
            with Journal.for_profile(get_hermes_home()) as journal:
                states = [State.from_dict(json.loads(row[0])) for row in
                          journal.db.execute('SELECT snapshot FROM delivery')]
                print(json.dumps(dict(deliveries=[summary(s) for s in states])))
            return 0
        if command == 'smoke':
            from ..delivery.smoke import run
            print(json.dumps(run(), indent=2))
            return 0
        if command == 'live-probe':
            from ..delivery.live_probe import observe_actor, run
            actors = (observe_actor('builder', args.builder), observe_actor('reviewer', args.reviewer))
            print(json.dumps(run(actors=actors, seconds=args.seconds), indent=2))
            return 0
        raise ValueError('unsupported command')
    except Exception as exc:
        print('cpipe diagnostic unavailable or failed: ' + type(exc).__name__, file=sys.stderr)
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog='cpipe')
    setup_parser(parser)
    return handle(parser.parse_args(argv))
