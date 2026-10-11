"""v2 is the only installed lifecycle; no legacy fallbacks."""
import json
import time
from dataclasses import replace

import pytest

from cpipe.delivery.journal import Journal
from cpipe.delivery.service import Event, Service
from .test_artifact import repo, git
from .test_check_runner import setup_check, pytest_check


class Context:
    def __init__(self):
        self.tools, self.commands, self.hooks, self.cli = {}, {}, {}, {}
    def register_tool(self, **kw):
        self.tools[kw['name']] = kw
    def register_command(self, name, handler, **kw):
        self.commands[name] = handler
    def register_hook(self, name, handler):
        self.hooks[name] = handler
    def register_cli_command(self, **kw):
        self.cli[kw['name']] = kw


def test_registration_is_v2_only():
    import cpipe
    ctx = Context()
    cpipe.register(ctx)
    assert set(ctx.tools) == {'cpipe_execute_intent', 'cpipe_approve_plan'}
    assert not ctx.hooks
    params = ctx.tools['cpipe_approve_plan']['schema']['parameters']
    assert set(params['properties']) == {'delivery_id', 'plan_revision'}
    assert set(ctx.commands) == {'cp-run', 'cp-status', 'cp-stop'}


@pytest.mark.parametrize('old', ['run', 'once', 'round', 'configure'])
def test_legacy_daemon_cli_is_not_available(old):
    from cpipe.runtime.cli import main
    with pytest.raises(SystemExit) as exc:
        main([old])
    assert exc.value.code == 2


def test_execute_intent_rejects_legacy_payload():
    from cpipe import native
    ctx = Context()
    native.register(ctx)
    out = json.loads(ctx.tools['cpipe_execute_intent']['handler'](
        {'token': 'old', 'operation': 'build', 'arguments': {'task_id': 'legacy'}}, session_id='s1'))
    assert 'error' in out


def test_slash_status_reads_v2_journal(repo, tmp_path, hermes_home, monkeypatch):
    from cpipe import native
    from gateway import session_context
    journal, service, state, spec = setup_check(repo, tmp_path)
    proposal = Event.from_dict(journal.event('d1', 'proposal')['fact'])
    journal.db.close()
    with Journal.for_profile(hermes_home) as journal:
        Service(journal).accept(proposal)
    monkeypatch.setattr(session_context, 'get_session_env', lambda key: 's1' if key == 'HERMES_SESSION_ID' else '')
    ctx = Context()
    native.register(ctx)
    out = json.loads(ctx.commands['cp-status']('d1'))
    assert out['delivery_id'] == 'd1'
    assert out['phase'] == 'proposal'
    assert out['engine_version'] == 2


def test_controller_uses_real_red_green_and_independent_review(repo, tmp_path):
    from cpipe.delivery.controller import propose, run
    from cpipe.delivery.workers import WorkerResult
    from cpipe.delivery.review import REVIEW_AXES
    from cpipe.runtime import hermes_api as h
    from .test_execution_plan import plan
    spec = pytest_check(timeout=20)
    (repo / 'test_behavior.py').write_text('from double import double\ndef test_double(): assert double(2) == 4\n')
    p = plan(worktree=str(repo), common_git_dir=str(repo / '.git'),
        base_commit=git(repo, 'rev-parse', 'HEAD'), expires_at=int(time.time()) + 300,
        envelope=replace(plan().envelope, required=('behavior', 'review')),
        checks=(spec.identity(), __import__('cpipe.delivery.review', fromlist=['review_identity']).review_identity()),
        write_paths=('double.py',), interactive=False, transient_retries=0)
    h.kb.init_db(tmp_path / 'native.db')
    conn = h.kbc.connect(tmp_path / 'native.db')
    def execute(state, *, on_session, **kw):
        session = 'builder-session' if state.activity == 'implement' else 'review-session'
        on_session(session)
        if state.activity == 'implement':
            (repo / 'double.py').write_text('def double(n): return n * 2\n')
            return WorkerResult(session, {'status': 'done'}, dict(input=100, output=10, total=110, cache_read=80))
        return WorkerResult(session, dict(status='approved', artifact=state.artifact,
            reviewed_from=state.plan.base_commit, coverage=list(REVIEW_AXES), findings=[], verified_ids=[]),
            dict(input=50, output=5, total=55, cache_read=0))
    try:
        with Journal(tmp_path / 'controller.db') as journal:
            service = Service(journal)
            state = propose(service, p, (spec,))
            with pytest.raises(ValueError, match='approved'):
                run(service, 'd1', session_id='s1', profile_id='default', conn=conn, execute=execute,
                    evidence_root=tmp_path / 'evidence')
            service.accept(Event('approval', 'envelope_approved', 'd1', p.digest(), state.artifact,
                state.version, state.last_at, session_id='s1', profile_id='default', activity_id=state.active_intent))
            result = run(service, 'd1', session_id='s1', profile_id='default', conn=conn, execute=execute,
                evidence_root=tmp_path / 'evidence')
            assert result['phase'] == 'locally_ready'
            assert [s['activity'] for s in result['steps']] == ['tests', 'implement', 'checks', 'review']
            assert result['steps'][0]['outcomes'] == ['fail']
            assert result['steps'][-1]['outcomes'][-2:] == ['pass', 'pass']
            assert all(step['native_status'] == 'done' for step in result['steps'])
            assert result['tokens'] == dict(steps=2, input=150, output=15, total=165, cache_read=80, cache_write=0)
    finally:
        conn.close()


def test_controller_writes_a_missing_behavior_test_then_goes_red_green(repo, tmp_path):
    """The realistic path: the behavior test does not exist until the builder writes it."""
    from cpipe.delivery.controller import propose, run
    from cpipe.delivery.workers import WorkerResult
    from cpipe.delivery.review import REVIEW_AXES, review_identity
    from cpipe.runtime import hermes_api as h
    from .test_execution_plan import plan
    spec = pytest_check(timeout=20)
    p = plan(worktree=str(repo), common_git_dir=str(repo / '.git'),
        base_commit=git(repo, 'rev-parse', 'HEAD'), expires_at=int(time.time()) + 300,
        envelope=replace(plan().envelope, required=('behavior', 'review')),
        checks=(spec.identity(), review_identity()),
        write_paths=('double.py', 'test_behavior.py'), interactive=False, transient_retries=0)
    h.kb.init_db(tmp_path / 'native.db')
    conn = h.kbc.connect(tmp_path / 'native.db')
    def execute(state, *, on_session, **kw):
        session = 'review-session' if state.activity == 'review' else 'builder-session'
        on_session(session)
        if state.activity == 'write_tests':
            (repo / 'test_behavior.py').write_text('from double import double\ndef test_double(): assert double(2) == 4\n')
        elif state.activity == 'implement':
            (repo / 'double.py').write_text('def double(n): return n * 2\n')
        else:
            return WorkerResult(session, dict(status='approved', artifact=state.artifact,
                reviewed_from=state.plan.base_commit, coverage=list(REVIEW_AXES), findings=[], verified_ids=[]))
        return WorkerResult(session, {'status': 'done'})
    try:
        with Journal(tmp_path / 'controller.db') as journal:
            service = Service(journal)
            state = propose(service, p, (spec,))
            service.accept(Event('approval', 'envelope_approved', 'd1', p.digest(), state.artifact,
                state.version, state.last_at, session_id='s1', profile_id='default', activity_id=state.active_intent))
            result = run(service, 'd1', session_id='s1', profile_id='default', conn=conn, execute=execute,
                evidence_root=tmp_path / 'evidence')
            assert [s['activity'] for s in result['steps']] == [
                'tests', 'write_tests', 'tests', 'implement', 'checks', 'review']
            assert result['phase'] == 'locally_ready'
    finally:
        conn.close()


def test_controller_recovers_a_step_that_crashed(repo, tmp_path):
    """A crashed step must not leave the delivery bound to a dead card forever."""
    from cpipe.delivery.controller import owned, propose, run
    from cpipe.delivery.workers import WorkerResult
    from cpipe.delivery.review import REVIEW_AXES, review_identity
    from cpipe.runtime import hermes_api as h
    from .test_execution_plan import plan
    spec = pytest_check(timeout=20)
    (repo / 'test_behavior.py').write_text('from double import double\ndef test_double(): assert double(2) == 4\n')
    p = plan(worktree=str(repo), common_git_dir=str(repo / '.git'),
        base_commit=git(repo, 'rev-parse', 'HEAD'), expires_at=int(time.time()) + 300,
        envelope=replace(plan().envelope, required=('behavior', 'review')),
        checks=(spec.identity(), review_identity()),
        write_paths=('double.py',), interactive=False, transient_retries=0)
    h.kb.init_db(tmp_path / 'native.db')
    conn = h.kbc.connect(tmp_path / 'native.db')
    crashes = [RuntimeError('worker died')]
    def execute(state, *, on_session, **kw):
        session = 'review-session' if state.activity == 'review' else 'builder-session'
        on_session(session)
        if state.activity == 'implement':
            if crashes:
                raise crashes.pop()
            (repo / 'double.py').write_text('def double(n): return n * 2\n')
            return WorkerResult(session, {'status': 'done'})
        return WorkerResult(session, dict(status='approved', artifact=state.artifact,
            reviewed_from=state.plan.base_commit, coverage=list(REVIEW_AXES), findings=[], verified_ids=[]))
    kw = dict(session_id='s1', profile_id='default', conn=conn, execute=execute, evidence_root=tmp_path / 'e')
    try:
        with Journal(tmp_path / 'controller.db') as journal:
            service = Service(journal)
            state = propose(service, p, (spec,))
            service.accept(Event('approval', 'envelope_approved', 'd1', p.digest(), state.artifact,
                state.version, state.last_at, session_id='s1', profile_id='default', activity_id=state.active_intent))
            with pytest.raises(RuntimeError):
                run(service, 'd1', **kw)
            assert not owned(service, 'd1', 's1', 'default').run_id
            result = run(service, 'd1', **kw)
            assert result['phase'] == 'locally_ready'
            assert [s['activity'] for s in result['steps']] == ['implement', 'checks', 'review']
    finally:
        conn.close()
