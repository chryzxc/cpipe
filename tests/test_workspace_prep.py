"""A worker's first turn gets a usable workspace: deps linked from the main checkout, or the
parent's worktree when it was handed an empty scratch dir. Never a reason to block."""
import sqlite3
import subprocess

from software_delivery import workspace_prep


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def repo_with_worktree(tmp_path):
    main = tmp_path / "main"
    (main / "client").mkdir(parents=True)
    for rel in (".", "client"):
        (main / rel / "package.json").write_text("{}")
        (main / rel / "node_modules" / "vitest").mkdir(parents=True)
    git(main, "init", "-q"); git(main, "add", "package.json", "client/package.json")
    git(main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    wt = tmp_path / "wt"
    git(main, "worktree", "add", "-q", str(wt))
    return main, wt


def test_first_turn_links_missing_node_modules_once(tmp_path, monkeypatch):
    main, wt = repo_with_worktree(tmp_path)
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(wt))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    note = workspace_prep.prepare_workspace(is_first_turn=True)
    assert "symlinked" in note["context"] and "client" in note["context"]
    assert (wt / "client" / "node_modules" / "vitest").is_dir()
    assert (wt / "node_modules").resolve() == (main / "node_modules").resolve()
    status = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True)
    assert "node_modules" not in status.stdout                              # never committable
    assert workspace_prep.prepare_workspace(is_first_turn=True) is None     # nothing left to do
    assert workspace_prep.prepare_workspace(is_first_turn=False) is None


def test_empty_scratch_workspace_points_at_parent_worktree(tmp_path, monkeypatch):
    _, wt = repo_with_worktree(tmp_path)
    scratch = tmp_path / "scratch"; scratch.mkdir()
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE tasks (id TEXT, workspace_path TEXT, completed_at REAL, created_at REAL);"
                       "CREATE TABLE task_links (parent_id TEXT, child_id TEXT);")
    conn.execute("INSERT INTO tasks VALUES ('t_parent', ?, 1, 1)", (str(wt),))
    conn.execute("INSERT INTO task_links VALUES ('t_parent', 't_child')")
    conn.commit(); conn.close()
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(scratch))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_child")
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    note = workspace_prep.prepare_workspace(is_first_turn=True)
    assert str(wt) in note["context"] and "Do not block" in note["context"]


def test_outside_kanban_workers_it_does_nothing(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_WORKSPACE", raising=False)
    assert workspace_prep.prepare_workspace(is_first_turn=True) is None


def test_project_profile_flags_a_worktree_off_the_pr_base(tmp_path, monkeypatch):
    main, wt = repo_with_worktree(tmp_path)
    git(main, "branch", "develop")
    git(main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "on develop only")
    git(main, "update-ref", "refs/remotes/origin/develop", "HEAD")  # origin/develop is ahead of wt's base
    (tmp_path / "delivery").mkdir()
    (tmp_path / "delivery" / "projects.yaml").write_text(
        "projects:\n  my-app:\n    base_branch: develop\n    branch_prefix: feat/\n    env: cp .env.example .env\n")
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks (id TEXT, project_id TEXT)")
    conn.execute("INSERT INTO tasks VALUES ('t_1', 'my-app')")
    conn.commit(); conn.close()
    for k, v in (("HERMES_HOME", tmp_path), ("HERMES_KANBAN_DB", db), ("HERMES_KANBAN_WORKSPACE", wt),
                 ("HERMES_KANBAN_TASK", "t_1")):
        monkeypatch.setenv(k, str(v))
    note = workspace_prep.prepare_workspace(is_first_turn=True)["context"]
    assert "not based on origin/develop" in note and "should start with `feat/`" in note
    assert "cp .env.example .env" in note
    assert workspace_prep.project_profile("other-app", tmp_path) == {}
