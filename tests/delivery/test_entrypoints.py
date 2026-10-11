"""One installed lifecycle; old entry points are rejected."""
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


def test_registration_exposes_only_current_tools():
    import cpipe
    ctx = Context()
    cpipe.register(ctx)
    assert set(ctx.tools) == {'cpipe_execute_intent', 'cpipe_approve_plan'}
    assert set(ctx.hooks) == {'on_session_start', 'pre_llm_call'}
    params = ctx.tools['cpipe_approve_plan']['schema']['parameters']
    assert set(params['properties']) == {'delivery_id', 'plan_revision'}
    assert set(ctx.commands) == {'cp-run', 'cp-status', 'cp-stop'}


@pytest.mark.parametrize('old', ['run', 'once', 'round', 'configure'])
def test_old_daemon_cli_is_not_available(old):
    from cpipe.runtime.cli import main
    with pytest.raises(SystemExit) as exc:
        main([old])
    assert exc.value.code == 2


def test_execute_intent_rejects_old_payload():
    from cpipe import native
    ctx = Context()
    native.register(ctx)
    out = json.loads(ctx.tools['cpipe_execute_intent']['handler'](
        {'token': 'old', 'operation': 'build', 'arguments': {'task_id': 'old'}}, session_id='s1'))
    assert 'error' in out


def test_slash_status_reads_journal(repo, tmp_path, hermes_home, monkeypatch):
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


def test_a_refusing_builder_is_replaced_without_stopping_the_run(repo, tmp_path):
    """A builder whose profile forbids edits declines; the next approved builder finishes the job."""
    from cpipe.delivery.controller import propose, run
    from cpipe.delivery.schema import Actor
    from cpipe.delivery.workers import WorkerResult
    from cpipe.delivery.review import REVIEW_AXES, review_identity
    from cpipe.runtime import hermes_api as h
    from .test_execution_plan import plan
    spec = pytest_check(timeout=20)
    p = plan(worktree=str(repo), common_git_dir=str(repo / '.git'),
        base_commit=git(repo, 'rev-parse', 'HEAD'), expires_at=int(time.time()) + 300,
        envelope=replace(plan().envelope, required=('behavior', 'review')),
        checks=(spec.identity(), review_identity()),
        actors=(Actor('builder', 'hermes', 'sentry', 'm', 'p'), Actor('builder', 'hermes', 'forge', 'm', 'p'),
                Actor('reviewer', 'hermes', 'aegis', 'm', 'p')),
        write_paths=('double.py', 'test_behavior.py'), interactive=False, transient_retries=0)
    h.kb.init_db(tmp_path / 'native.db')
    conn = h.kbc.connect(tmp_path / 'native.db')
    calls = []
    def execute(state, *, on_session, **kw):
        calls.append(state.activity)  # builders resume their bound session, like HermesWorker
        session = (state.activity != 'review' and state.builder_session) or f'{state.activity}-{len(calls)}'
        on_session(session)
        if state.activity == 'review':
            return WorkerResult(session, dict(status='approved', artifact=state.artifact,
                reviewed_from=state.plan.base_commit, coverage=list(REVIEW_AXES), findings=[], verified_ids=[]))
        if calls.count('write_tests') == 1:  # edits, then declines: its leftovers must not survive
            (repo / 'double.py').write_text('broken(\n')
            (repo / 'scratch.py').write_text('junk\n')
            git(repo, 'add', 'scratch.py')
            return WorkerResult(session, {'status': 'blocked', 'reason': 'my profile is read-only'})
        if state.activity == 'write_tests':
            (repo / 'test_behavior.py').write_text('from double import double\ndef test_double(): assert double(2) == 4\n')
        else:
            (repo / 'double.py').write_text('def double(n): return n * 2\n')
        return WorkerResult(session, {'status': 'done'})
    try:
        with Journal(tmp_path / 'controller.db') as journal:
            service = Service(journal)
            state = propose(service, p, (spec,))
            service.accept(Event('approval', 'envelope_approved', 'd1', p.digest(), state.artifact,
                state.version, state.last_at, session_id='s1', profile_id='default', activity_id=state.active_intent))
            result = run(service, 'd1', session_id='s1', profile_id='default', conn=conn, execute=execute,
                evidence_root=tmp_path / 'evidence')
            assert result['phase'] == 'locally_ready'
            assert result['recoveries'] == ['sentry declined write_tests: my profile is read-only']
            assert service.read('d1').builder == 'forge'
            assert not (repo / 'scratch.py').exists() and 'n * 2' in (repo / 'double.py').read_text()
            assert git(repo, 'diff', '--cached', '--name-only') == ''
    finally:
        conn.close()


def test_controller_recovers_a_step_that_crashed(repo, tmp_path, monkeypatch):
    """A crashed step must not leave the delivery bound to a dead card forever."""
    from cpipe.delivery.controller import propose, run
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
    from cpipe.delivery import controller
    monkeypatch.setattr(controller, 'BACKOFF', 0)
    crashes, seen_notes = [RuntimeError('worker died')], []
    def execute(state, *, on_session, **kw):
        session = 'review-session' if state.activity == 'review' else 'builder-session'
        on_session(session)
        if state.activity == 'implement':
            if getattr(execute, 'note', ''):
                seen_notes.append(execute.note)
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
            result = run(service, 'd1', **kw)  # recovered inside the same run, no restart needed
            assert result['phase'] == 'locally_ready'
            assert [s['activity'] for s in result['steps']] == ['tests', 'implement', 'checks', 'review']
            assert result['recoveries'] == ['forge failed implement (RuntimeError); retrying']
            assert 'previous attempt at this step failed: RuntimeError' in seen_notes[0]
    finally:
        conn.close()


def test_a_builder_that_keeps_failing_is_retried_then_replaced(repo, tmp_path, monkeypatch):
    """resume with the reason -> fresh session -> next approved builder; the run never stops."""
    from cpipe.delivery import controller
    from cpipe.delivery.controller import propose, run
    from cpipe.delivery.schema import Actor
    from cpipe.delivery.workers import WorkerResult
    from cpipe.delivery.review import REVIEW_AXES, review_identity
    from cpipe.runtime import hermes_api as h
    from .test_execution_plan import plan
    monkeypatch.setattr(controller, 'BACKOFF', 0)
    spec = pytest_check(timeout=20)
    (repo / 'test_behavior.py').write_text('from double import double\ndef test_double(): assert double(2) == 4\n')
    p = plan(worktree=str(repo), common_git_dir=str(repo / '.git'),
        base_commit=git(repo, 'rev-parse', 'HEAD'), expires_at=int(time.time()) + 300,
        envelope=replace(plan().envelope, required=('behavior', 'review')),
        checks=(spec.identity(), review_identity()),
        actors=(Actor('builder', 'hermes', 'forge', 'm', 'p'), Actor('builder', 'hermes', 'atlas', 'm', 'p'),
                Actor('reviewer', 'hermes', 'aegis', 'm', 'p')),
        write_paths=('double.py',), interactive=False, transient_retries=0)
    h.kb.init_db(tmp_path / 'native.db')
    conn = h.kbc.connect(tmp_path / 'native.db')
    resumed = []
    def execute(state, *, on_session, **kw):
        if state.activity == 'review':
            on_session('review-session')
            return WorkerResult('review-session', dict(status='approved', artifact=state.artifact,
                reviewed_from=state.plan.base_commit, coverage=list(REVIEW_AXES), findings=[], verified_ids=[]))
        resumed.append(state.builder_session)
        session = state.builder_session or f'{state.builder or "forge"}-{len(resumed)}'
        on_session(session)
        if state.builder != 'atlas':
            (repo / 'double.py').write_text('half done\n')  # every failed attempt is undone
            return WorkerResult(session, {'status': 'done', 'note': 'not json the controller can use'}
                                ) if len(resumed) == 1 else 1 / 0
        (repo / 'double.py').write_text('def double(n): return n * 2\n')
        return WorkerResult(session, {'status': 'done'})
    try:
        with Journal(tmp_path / 'controller.db') as journal:
            service = Service(journal)
            state = propose(service, p, (spec,))
            service.accept(Event('approval', 'envelope_approved', 'd1', p.digest(), state.artifact,
                state.version, state.last_at, session_id='s1', profile_id='default', activity_id=state.active_intent))
            result = run(service, 'd1', session_id='s1', profile_id='default', conn=conn, execute=execute,
                         evidence_root=tmp_path / 'evidence')
            assert result['phase'] == 'locally_ready', result
            assert [r.split(' (')[0] for r in result['recoveries']] == ['forge failed implement'] * 3
            assert result['recoveries'][-1].endswith('handing over')
            assert resumed == ['', 'forge-1', '', '']  # resume, then fresh, then atlas in a new session
            assert service.read('d1').builder == 'atlas'
    finally:
        conn.close()


def test_a_run_past_its_budget_records_the_stop_instead_of_crashing(repo, tmp_path, monkeypatch):
    from cpipe.delivery import controller
    from cpipe.delivery.controller import propose, run
    from cpipe.delivery.review import review_identity
    from cpipe.runtime import hermes_api as h
    from .test_execution_plan import plan
    spec = pytest_check(timeout=20)
    p = plan(worktree=str(repo), common_git_dir=str(repo / '.git'),
        base_commit=git(repo, 'rev-parse', 'HEAD'), expires_at=int(time.time()) + 300,
        envelope=replace(plan().envelope, required=('behavior', 'review')),
        checks=(spec.identity(), review_identity()), write_paths=('double.py',), interactive=False,
        transient_retries=0)
    h.kb.init_db(tmp_path / 'native.db')
    conn = h.kbc.connect(tmp_path / 'native.db')
    try:
        with Journal(tmp_path / 'controller.db') as journal:
            service = Service(journal)
            state = propose(service, p, (spec,))
            service.accept(Event('approval', 'envelope_approved', 'd1', p.digest(), state.artifact,
                state.version, state.last_at, session_id='s1', profile_id='default', activity_id=state.active_intent))
            later = time.time() + 3600  # e.g. the run was parked or Hermes was down meanwhile
            monkeypatch.setattr(controller.time, 'time', lambda: later)
            result = run(service, 'd1', session_id='s1', profile_id='default', conn=conn,
                         execute=lambda *a, **kw: 1 / 0, evidence_root=tmp_path / 'evidence')
            assert result['phase'] == 'stopped' and result['steps'] == [] and 'recoveries' not in result
    finally:
        conn.close()


def test_roll_back_restores_files_index_branch_and_commits(repo, tmp_path):
    from cpipe.delivery.hermes_worker import keep, roll_back
    from .test_execution_plan import plan
    p = plan(worktree=str(repo), common_git_dir=str(repo / '.git'), base_commit=git(repo, 'rev-parse', 'HEAD'))
    (repo / 'double.py').write_text('def double(n): return n * 2\n')
    (repo / 'kept.py').write_text('kept\n')
    git(repo, 'add', 'kept.py')
    keep(p, tmp_path / 'good')
    staged = git(repo, 'diff', '--cached', '--name-only')
    (repo / 'double.py').write_text('broken\n')
    (repo / 'kept.py').unlink()
    (repo / 'junk.py').write_text('junk\n')
    git(repo, 'add', '-A')
    git(repo, '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'worker commit')
    git(repo, 'checkout', '-qb', 'elsewhere')
    assert roll_back(p, tmp_path / 'good')
    assert git(repo, 'symbolic-ref', '--short', 'HEAD') == p.envelope.branch
    assert git(repo, 'rev-parse', 'HEAD') == p.base_commit
    assert (repo / 'double.py').read_text() == 'def double(n): return n * 2\n'
    assert (repo / 'kept.py').read_text() == 'kept\n' and not (repo / 'junk.py').exists()
    assert git(repo, 'diff', '--cached', '--name-only') == staged == 'kept.py'


@pytest.mark.parametrize('drift, adopted', [('double.py', True), ('outside.py', False)])
def test_leftovers_with_nothing_to_roll_back_to_are_adopted_only_inside_the_approved_paths(
        repo, tmp_path, monkeypatch, drift, adopted):
    """A crashed step from an earlier process left edits behind and no verified snapshot exists."""
    from cpipe.delivery import controller
    from cpipe.delivery.controller import propose, run
    from cpipe.delivery.workers import WorkerResult
    from cpipe.delivery.review import REVIEW_AXES, review_identity
    from cpipe.runtime import hermes_api as h
    from .test_execution_plan import plan
    monkeypatch.setattr(controller, 'BACKOFF', 0)
    spec = pytest_check(timeout=20)
    (repo / 'test_behavior.py').write_text('from double import double\ndef test_double(): assert double(2) == 4\n')
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
        if state.activity == 'review':
            return WorkerResult(session, dict(status='approved', artifact=state.artifact,
                reviewed_from=state.plan.base_commit, coverage=list(REVIEW_AXES), findings=[], verified_ids=[]))
        (repo / 'double.py').write_text('def double(n): return n * 2\n')
        return WorkerResult(session, {'status': 'done'})
    try:
        with Journal(tmp_path / 'controller.db') as journal:
            service = Service(journal)
            state = propose(service, p, (spec,))
            service.accept(Event('approval', 'envelope_approved', 'd1', p.digest(), state.artifact,
                state.version, state.last_at, session_id='s1', profile_id='default', activity_id=state.active_intent))
            (repo / drift).write_text('def double(n): return n + 1  # half-finished\n')
            kw = dict(session_id='s1', profile_id='default', conn=conn, execute=execute,
                      evidence_root=tmp_path / 'evidence')
            if adopted:
                result = run(service, 'd1', **kw)
                assert result['phase'] == 'locally_ready', result
            else:
                with pytest.raises(ValueError, match='outside the approved paths'):
                    run(service, 'd1', **kw)
                assert (repo / drift).exists()  # never deletes work it cannot attribute
    finally:
        conn.close()
