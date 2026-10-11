from cpipe.delivery.check_runner import _environment


def test_check_environment_never_hashes_or_forwards_arbitrary_credential_values(monkeypatch):
    monkeypatch.setenv('CUSTOM_TEST_SETTING', 'DO_NOT_RETAIN_TEST_VALUE')
    before_env, before_identity = _environment('sh')
    monkeypatch.setenv('CUSTOM_TEST_SETTING', 'DIFFERENT_PRIVATE_VALUE')
    after_env, after_identity = _environment('sh')
    assert 'CUSTOM_TEST_SETTING' not in before_env
    assert 'CUSTOM_TEST_SETTING' not in after_env
    assert before_identity == after_identity


def test_a_different_program_is_a_different_environment():
    assert _environment('sh')[1] != _environment('env')[1]
