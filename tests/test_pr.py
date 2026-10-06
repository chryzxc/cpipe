"""The public-text scan, CI failure handling, QA modes and check stages, on a fixture repo and fake gh."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from cpipe import pr, quality, round as rnd

from fixtures.make_repo import install, make_repo

HERE = Path(__file__).parent


def commit(repo: Path, rel: str, text: str, msg: str) -> None:
    (repo / rel).write_text(text)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", rel], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", msg], cwd=repo, check=True)


def test_leaks_and_redact(tmp_path):
    fx = make_repo(tmp_path, "python")
    commit(fx.repo, "notes.py", "CACHE = '/Users/someone/.cache'\n", "chore: cache for t_0123abcd")
    assert pr.leaks(fx.repo, "origin/main") == ["/Users/someone/.cache'", "t_0123abcd"]
    sha = "a" * 40
    text = pr.redact(f"Built in {fx.repo}/calc.py at {sha}, see https://x/commit/{sha}", fx.repo)
    assert str(fx.repo) not in text and text.count(sha) == 1 and "[redacted]" in text


def test_publish_sends_leaks_back(tmp_path):
    fx = make_repo(tmp_path, "python")
    state = {"base": "main", "request": "r"}
    assert pr.publish(fx.repo, state, "cp/x")[0] == "fix"                 # no commits yet
    commit(fx.repo, "notes.py", "P = '/home/someone/x'\n", "chore: notes")
    out, detail = pr.publish(fx.repo, state, "cp/x")
    assert out == "fix" and "/home/someone/x" in detail


def test_ci_failure_returns_the_log(tmp_path, monkeypatch):
    fx = make_repo(tmp_path, "python")
    (fx.repo / ".github" / "workflows").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").symlink_to(HERE / "fake_gh.py")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_LOG", str(tmp_path / "gh.jsonl"))
    monkeypatch.setenv("FAKE_GH_CHECKS", json.dumps([
        {"name": "test", "state": "FAILURE", "bucket": "fail", "link": "https://github.com/a/b/actions/runs/42/job/1"},
        {"name": "lint", "state": "SUCCESS", "bucket": "pass", "link": ""}]))
    out, detail = pr.ci(fx.repo, {"pr_url": "https://github.com/a/b/pull/1"}, 1)
    assert out == "fail" and "FAIL test_add" in detail
    assert ["run", "view", "42", "--log-failed"] in [json.loads(l) for l in (tmp_path / "gh.jsonl").read_text().splitlines()]
    monkeypatch.setenv("FAKE_GH_CHECKS", json.dumps([{"name": "test", "bucket": "pass"}]))
    assert pr.ci(fx.repo, {"pr_url": "u"}, 1)[0] == "ok"
    assert pr.ci(fx.repo, {}, 1) == ("ok", "no CI")


def test_check_stages(tmp_path, hermes_home):
    fx = make_repo(tmp_path, "python")
    fx.quality["checks"].append({"name": "secrets", "run": "exit 1", "stage": "security", "projects": [fx.project]})
    install([fx], hermes_home)
    commit(fx.repo, "calc.py", "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n", "fix")
    assert quality.run_checks(fx.repo, "origin/main", fx.project) == []
    assert [f.split(":")[0] for f in quality.run_checks(fx.repo, "origin/main", fx.project, stage="security")] == ["secrets"]


def test_qa_modes(tmp_path, hermes_home, monkeypatch):
    fx = make_repo(tmp_path, "python")
    install([fx], hermes_home)
    r = object.__new__(rnd.Round)
    r.state = {"project": fx.project}
    r.ws = fx.repo
    monkeypatch.setattr(rnd.test_env, "start", lambda ws, p: {"ok": False, "error": "wtg is not installed"})
    assert r.qa() == (None, "QA: skipped (wtg is not installed)")
    monkeypatch.setattr(rnd.test_env, "start", lambda ws, p: {"ok": False, "error": "not answering", "log_tail": {"web": "boom"}})
    ok, text = r.qa()
    assert ok is False and "boom" in text
    fx.profile["qa"] = "off"
    install([fx], hermes_home)
    assert r.qa() == (None, "QA: off for this project")
