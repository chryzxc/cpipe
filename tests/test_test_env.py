import json
import subprocess

from cpipe import test_env, workspace_prep


def test_runs_the_saved_recipe_under_wtg_and_reports_what_it_serves(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "fix/x", str(wt)])
    subprocess.run(["git", "-C", str(wt), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q",
                    "--allow-empty", "-m", "x"])
    monkeypatch.setattr(test_env.submit, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(test_env.submit, "_card_worktree", lambda card: str(wt))
    monkeypatch.setattr(test_env.submit, "_task_row", lambda card: {"project_id": "shop"})
    monkeypatch.setattr(test_env.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(workspace_prep, "project_profile", lambda p, h=None: {})
    out = json.loads(test_env.run({"target": "t_1"}))
    assert not out["ok"] and "no test_env recipe" in out["error"]

    recipe = {"setup": ["touch ready"], "services": {"api": "node app.js", "web": "npm run serve"}}
    monkeypatch.setattr(workspace_prep, "project_profile", lambda p, h=None: {"test_env": recipe})
    monkeypatch.setattr(test_env, "_wtg_env", lambda w: {"WG_URL": "https://x", "WG_API_URL": "https://api.x"})
    started = []
    real = subprocess.Popen
    monkeypatch.setattr(test_env.subprocess, "Popen",
                        lambda argv, **kw: started.append(argv) if argv[0] == "wtg" else real(argv, **kw))
    monkeypatch.setattr(test_env, "_answers", lambda url: True)
    out = json.loads(test_env.run({"target": "t_1"}))
    assert out["ok"] and out["branch"] == "fix/x" and out["urls"] == {"api": "https://api.x", "web": "https://x"}
    assert (wt / "ready").exists()
    assert started[0] == ["wtg", "run", "api", "--", "sh", "-c", "node app.js"]

    monkeypatch.setattr(test_env, "_answers", lambda url: "api" not in url)
    monkeypatch.setattr(test_env, "READY_SECONDS", 0)
    out = json.loads(test_env.run({"target": "t_1"}))
    assert not out["ok"] and list(out["log_tail"]) == ["api"]

    envs = iter([{}, {"WG_API_URL": "https://api.x", "WG_WEB_URL": "https://x"}])  # services not registered yet
    monkeypatch.setattr(test_env, "_wtg_env", lambda w: next(envs, {"WG_API_URL": "https://api.x", "WG_WEB_URL": "https://x"}))
    monkeypatch.setattr(test_env, "_answers", lambda url: True)
    out = json.loads(test_env.run({"target": "t_1"}))
    assert out["ok"] and out["urls"] == {"api": "https://api.x", "web": "https://x"}


def test_a_branch_no_card_owns_gets_its_own_detached_worktree(tmp_path, monkeypatch):
    """Running a PR cpipe did not open became a whole build-and-review delivery; it only needs a checkout."""
    git = lambda *a: subprocess.run(["git", *a], check=True, capture_output=True)
    git("init", "-q", "--bare", "-b", "main", str(tmp_path / "remote.git"))
    git("clone", "-q", str(tmp_path / "remote.git"), str(tmp_path / "repo"))
    git("-C", str(tmp_path / "repo"), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "x")
    git("-C", str(tmp_path / "repo"), "push", "-q", "origin", "HEAD:feat/report")
    monkeypatch.setattr(test_env.submit, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(test_env.submit, "_card_for_pr", lambda ref: None)
    monkeypatch.setattr(test_env.submit, "_project_repo", lambda p: str(tmp_path / "repo") if p == "app" else None)
    monkeypatch.setattr(workspace_prep, "project_profile", lambda p, h=None: {})

    out = json.loads(test_env.run({"target": "feat/report"}))
    assert not out["ok"] and "pass project=" in out["error"]
    out = json.loads(test_env.run({"target": "feat/report", "project": "app"}))
    assert "no test_env recipe for project 'app'" in out["error"]  # it got past the checkout
    wt = tmp_path / "repo" / ".worktrees" / "test-feat-report"
    head = lambda d: subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    assert head(wt) == head(tmp_path / "repo")
    json.loads(test_env.run({"target": "feat/report", "project": "app"}))  # a rerun reuses it
