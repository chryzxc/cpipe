import site

from cpipe.delivery.check_runner import _environment


def test_pytest_environment_never_hashes_or_forwards_arbitrary_credential_values(monkeypatch):
    monkeypatch.setenv('CUSTOM_TEST_SETTING', 'DO_NOT_RETAIN_TEST_VALUE')
    before_env, before_identity = _environment()
    monkeypatch.setenv('CUSTOM_TEST_SETTING', 'DIFFERENT_PRIVATE_VALUE')
    after_env, after_identity = _environment()
    assert 'CUSTOM_TEST_SETTING' not in before_env
    assert 'CUSTOM_TEST_SETTING' not in after_env
    assert before_identity == after_identity


def test_repeated_import_path_does_not_change_subprocess_environment_identity(monkeypatch):
    before = _environment()[1]
    monkeypatch.syspath_prepend(site.getsitepackages()[0])
    assert _environment()[1] == before
