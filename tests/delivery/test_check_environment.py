from cpipe.delivery.check_runner import CommandCheck, _environment, frozen_environment


def test_check_environment_never_hashes_or_forwards_arbitrary_credential_values(monkeypatch):
    monkeypatch.setenv('CUSTOM_TEST_SETTING', 'DO_NOT_RETAIN_TEST_VALUE')
    before_env, before_identity = _environment('sh', frozen_environment())
    monkeypatch.setenv('CUSTOM_TEST_SETTING', 'DIFFERENT_PRIVATE_VALUE')
    after_env, after_identity = _environment('sh', frozen_environment())
    assert 'CUSTOM_TEST_SETTING' not in before_env
    assert 'CUSTOM_TEST_SETTING' not in after_env
    assert before_identity == after_identity


def test_a_different_program_is_a_different_environment():
    assert _environment('sh', frozen_environment())[1] != _environment('env', frozen_environment())[1]


def test_a_check_approved_in_one_process_runs_the_same_in_another(monkeypatch, tmp_path):
    """The app restarts (or another chat's process takes the run over) with a different PATH, HOME and
    TMPDIR: the approved check keeps its identity and runs with the settings frozen at approval."""
    check = CommandCheck('behavior', ('sh', '-c', 'true'), ('t',))
    approved = check.identity()
    monkeypatch.setenv('PATH', str(tmp_path))  # sh is not on this PATH at all
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    resumed = CommandCheck('behavior', ('sh', '-c', 'true'), ('t',), env=check.env)
    assert resumed.identity() == approved
    assert resumed.environment()[0]['PATH'] != str(tmp_path)


def test_a_missing_program_is_named():
    check = CommandCheck('behavior', ('no-such-program-xyz',), env=(('PATH', '/nonexistent'),))
    try:
        check.identity()
    except ValueError as exc:
        assert 'no-such-program-xyz' in str(exc)
    else:
        raise AssertionError('a missing program must be refused')
