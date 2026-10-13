from cpipe.delivery.schema import Actor
from cpipe.delivery.workers import WorkerResult
from .test_execution_plan import plan


def test_reproducible_native_probe_uses_workers_and_never_claims_ui_consent(tmp_path):
    from cpipe.delivery.live_probe import run
    actors = plan().actors
    sessions = []
    def execute(state, *, on_session, heartbeat, **kwargs):
        session = 'review-session' if state.activity == 'review' else 'builder-session'
        sessions.append(session)
        on_session(session)
        heartbeat()
        if state.activity == 'implement':
            from pathlib import Path
            (Path(state.plan.worktree) / 'double.py').write_text('def double(n): return n * 2\n')
            payload = {'status': 'done'}
        else:
            from cpipe.delivery.review import REVIEW_AXES
            payload = dict(status='approved', artifact=state.artifact,
                reviewed_from=state.plan.base_commit, coverage=list(REVIEW_AXES), findings=[], verified_ids=[])
        return WorkerResult(session, payload)
    result = run(actors=actors, scratch=tmp_path, execute=execute)
    assert result['phase'] == 'locally_ready'
    assert [v['activity'] for v in result['steps']] == ['checks', 'implement', 'checks', 'review']
    assert all(v['native_status'] == 'done' for v in result['steps'])
    assert sessions == ['builder-session', 'review-session']
    assert result['approval'] == 'synthetic-harness-only'
    assert result['production_wiring'] is False


def test_probe_actor_resolution_requires_explicit_available_model_and_provider(tmp_path, monkeypatch):
    from cpipe.delivery.live_probe import observe_actor
    from cpipe import routing
    monkeypatch.setattr(routing, 'profile_home', lambda _: tmp_path)
    (tmp_path / 'config.yaml').write_text('model:\n  default: frozen-model\n  provider: frozen-provider\n')
    assert observe_actor('builder', 'example') == Actor('builder', 'hermes', 'example', 'frozen-model', 'frozen-provider')
    (tmp_path / 'config.yaml').write_text('model:\n  default: only-model\n')
    import pytest
    with pytest.raises(ValueError):
        observe_actor('builder', 'example')
