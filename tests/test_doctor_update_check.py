import software_delivery as plugin


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_source_status_up_to_date(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "rev-parse" in cmd:
            return FakeCompleted(stdout="aaa111bbb222\n")
        return FakeCompleted(stdout="aaa111bbb222\trefs/heads/main\n")

    monkeypatch.setattr(plugin.subprocess, "run", fake_run)

    status = plugin._workflow_source_status()

    assert "aaa111bbb222" in status
    assert "up to date" in status
    assert len(calls) == 2


def test_source_status_behind(monkeypatch):
    def fake_run(cmd, **kwargs):
        if "rev-parse" in cmd:
            return FakeCompleted(stdout="aaa111bbb222\n")
        return FakeCompleted(stdout="ddd444ccc333\trefs/heads/main\n")

    monkeypatch.setattr(plugin.subprocess, "run", fake_run)

    status = plugin._workflow_source_status()

    assert "behind origin/main" in status
    assert "git pull" in status


def test_source_status_offline(monkeypatch):
    def fake_run(cmd, **kwargs):
        if "rev-parse" in cmd:
            return FakeCompleted(stdout="aaa111bbb222\n")
        return FakeCompleted(returncode=128, stdout="")

    monkeypatch.setattr(plugin.subprocess, "run", fake_run)

    status = plugin._workflow_source_status()

    assert "update check skipped" in status
    assert "aaa111bbb222" in status


def test_source_status_git_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("no git")

    monkeypatch.setattr(plugin.subprocess, "run", fake_run)

    status = plugin._workflow_source_status()

    assert "unknown" in status or "skipped" in status


def test_version_consistency():
    plugin_yaml = (plugin._REPO_ROOT / "plugin.yaml").read_text()
    pyproject = (plugin._REPO_ROOT / "pyproject.toml").read_text()
    import re
    plugin_version = re.search(r"version:\s*(\S+)", plugin_yaml).group(1)
    pyproject_version = re.search(r'version\s*=\s*"([^"]+)"', pyproject).group(1)
    assert plugin_version == pyproject_version
    assert plugin_version == "0.3.0"
