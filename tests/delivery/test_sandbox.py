"""Real OS containment probes; never touch operator data or request a model."""
import json
import os
from pathlib import Path
import sys

import pytest

from cpipe.delivery.sandbox import Sandbox, SandboxUnavailable

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="real containment probes require macOS sandbox-exec")


def scope(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src/a.py").write_text("before")
    (root / ".git").mkdir()
    (root / ".git/config").write_text("unchanged")
    return root


@pytest.mark.parametrize("target", ["src/b.py", ".git/config", "../outside.txt"])
def test_real_process_cannot_write_outside_exact_scope(tmp_path, target):
    root = scope(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("unchanged")
    with Sandbox(root, ("src/a.py",)) as sandbox:
        result = sandbox.run([sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('bad')", target], timeout=10)
    assert result.returncode != 0, result.stdout
    assert outside.read_text() == "unchanged"
    assert (root / ".git/config").read_text() == "unchanged"
    assert not (root / "src/b.py").exists()


def test_exact_allowed_write_works_and_no_network_works(tmp_path):
    root = scope(tmp_path)
    with Sandbox(root, ("src/a.py",)) as sandbox:
        result = sandbox.run([sys.executable, "-c", "from pathlib import Path; Path('src/a.py').write_text('after')"], timeout=10)
        network = sandbox.run([sys.executable, "-c", "import socket; socket.socket().bind(('127.0.0.1', 0))"], timeout=10)
    assert result.returncode == 0, result.stdout
    assert (root / "src/a.py").read_text() == "after"
    assert network.returncode != 0


def test_readonly_process_cannot_write_even_inside_scope(tmp_path):
    root = scope(tmp_path)
    with Sandbox(root, (), readonly=True) as sandbox:
        result = sandbox.run([sys.executable, "-c", "from pathlib import Path; Path('src/a.py').write_text('bad')"], timeout=10)
    assert result.returncode != 0
    assert (root / "src/a.py").read_text() == "before"


@pytest.mark.parametrize("path", ["../outside", ".git/config", ".env", ".hermes/config.yaml", ".cpipe/state.json"])
def test_forbidden_paths_cannot_be_granted(tmp_path, path):
    root = scope(tmp_path)
    with pytest.raises(ValueError):
        Sandbox(root, (path,))


def test_symlinks_and_hardlinks_cannot_be_granted(tmp_path):
    root = scope(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (root / "src/link").symlink_to(outside)
    os.link(outside, root / "src/hardlink")
    for path in ("src/link", "src/hardlink"):
        with pytest.raises(ValueError):
            Sandbox(root, (path,))


@pytest.mark.parametrize("outside_scope", ["../outside.txt", "src/outside.txt"])
def test_child_cannot_replace_allowance_with_symlink_or_hardlink(tmp_path, outside_scope):
    root = scope(tmp_path)
    outside = root / outside_scope
    outside = outside.resolve()
    outside.write_text("outside")
    for action in ("os.symlink", "os.link"):
        with Sandbox(root, ("src/a.py",)) as sandbox:
            code = f"import os; os.unlink('src/a.py'); {action}({str(outside)!r}, 'src/a.py'); open('src/a.py','w').write('bad')"
            sandbox.run([sys.executable, "-c", code], timeout=10)
        assert outside.read_text() == "outside"
        path = root / "src/a.py"
        if path.is_symlink() or path.exists():
            path.unlink()
        path.write_text("before")


def test_backend_absence_fails_before_any_launch(tmp_path, monkeypatch):
    root = scope(tmp_path)
    monkeypatch.setattr("cpipe.delivery.sandbox.shutil.which", lambda _: None)
    with pytest.raises(SandboxUnavailable):
        Sandbox(root, ("src/a.py",))


def test_new_file_and_disposable_scratch_work_without_host_env(tmp_path, monkeypatch):
    root = scope(tmp_path)
    monkeypatch.setenv("SANDBOX_PRIVATE_TEST_SECRET", "must-not-leak")
    with Sandbox(root, ("src/new.py",)) as sandbox:
        code = "import os,json,tempfile; from pathlib import Path; Path('src/new.py').write_text('new'); p=Path(tempfile.mkstemp()[1]); p.write_text('scratch'); print(json.dumps({'leaked': 'SANDBOX_PRIVATE_TEST_SECRET' in os.environ, 'scratch': str(p)}))"
        result = sandbox.run([sys.executable, "-c", code], timeout=10)
        data = json.loads(result.stdout)
        scratch = Path(data["scratch"])
        assert scratch.is_relative_to(sandbox.scratch)
        assert not data["leaked"]
    assert not scratch.exists()
    assert (root / "src/new.py").read_text() == "new"


def test_timeout_is_enforced(tmp_path):
    root = scope(tmp_path)
    with Sandbox(root, ("src/a.py",)) as sandbox:
        with pytest.raises(__import__("subprocess").TimeoutExpired):
            sandbox.run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1)


def test_offline_boundary_cannot_fork_detached_descendants(tmp_path):
    root = scope(tmp_path)
    with Sandbox(root, ("src/a.py",)) as sandbox:
        result = sandbox.run([sys.executable, "-c", "import os; pid=os.fork(); os._exit(0)"], timeout=10)
    assert result.returncode != 0
    assert "PermissionError" in result.stdout


@pytest.mark.parametrize("name", [".env", ".env.test", ".hermes/config.yaml"])
def test_process_cannot_read_protected_fixture_files(tmp_path, name):
    root = scope(tmp_path)
    protected = root / name
    protected.parent.mkdir(exist_ok=True)
    protected.write_text("disposable-protected-fixture")
    with Sandbox(root, ("src/a.py",)) as sandbox:
        result = sandbox.run([sys.executable, "-c", "from pathlib import Path; import sys; print(Path(sys.argv[1]).read_text())", name], timeout=10)
    assert result.returncode != 0
    assert "disposable-protected-fixture" not in result.stdout
