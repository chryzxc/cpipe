import pytest


@pytest.fixture(autouse=True)
def _isolated_hermes_home(tmp_path_factory, monkeypatch):
    """Nothing under test may write the operator's live ~/.hermes (journal, notices, state)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path_factory.mktemp("hermes_home")))
