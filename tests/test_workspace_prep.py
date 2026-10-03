"""A worker's first turn gets a usable workspace: deps linked from the main checkout, or the
parent's worktree when it was handed an empty scratch dir. Never a reason to block."""
import sqlite3
import subprocess

from cpipe import workspace_prep


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
    monkeypatch.setattr(workspace_prep.shutil, "which", lambda cmd: None)  # no codegraph, no testscope
    monkeypatch.setattr(workspace_prep, "inline_scripts_refused", lambda: False)
    note = workspace_prep.prepare_workspace(is_first_turn=True)
    assert "symlinked" in note["context"] and "client" in note["context"]
    assert (wt / "client" / "node_modules" / "vitest").is_dir()
    assert (wt / "node_modules").resolve() == (main / "node_modules").resolve()
    status = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True)
    assert "node_modules" not in status.stdout                              # never committable
    assert workspace_prep.prepare_workspace(is_first_turn=True) is None     # nothing left to do
    assert workspace_prep.prepare_workspace(is_first_turn=False) is None
    monkeypatch.setattr(workspace_prep.shutil, "which", lambda cmd: cmd == "testscope" and "/bin/testscope")
    assert "`testscope`" in workspace_prep.prepare_workspace(is_first_turn=True)["context"]


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


def profile_on_develop(tmp_path, monkeypatch, body=""):
    main, wt = repo_with_worktree(tmp_path)
    git(main, "branch", "develop")
    git(main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "on develop only")
    git(main, "update-ref", "refs/remotes/origin/develop", "HEAD")  # origin/develop is ahead of wt's base
    (tmp_path / "delivery").mkdir()
    (tmp_path / "delivery" / "projects.yaml").write_text(
        "projects:\n  my-app:\n    base_branch: develop\n    branch_prefix: feat/\n    env: cp .env.example .env\n")
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks (id TEXT, project_id TEXT, body TEXT)")
    conn.execute("INSERT INTO tasks VALUES ('t_1', 'my-app', ?)", (body,))
    conn.commit(); conn.close()
    for k, v in (("HERMES_HOME", tmp_path), ("HERMES_KANBAN_DB", db), ("HERMES_KANBAN_WORKSPACE", wt),
                 ("HERMES_KANBAN_TASK", "t_1")):
        monkeypatch.setenv(k, str(v))
    return main, wt


def test_fresh_worktree_is_reset_onto_the_pr_base(tmp_path, monkeypatch):
    main, wt = profile_on_develop(tmp_path, monkeypatch)
    note = workspace_prep.prepare_workspace(is_first_turn=True)["context"]
    assert "reset onto origin/develop" in note and "not based on" not in note
    assert "should start with `feat/`" in note and "cp .env.example .env" in note
    head = lambda d: subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    assert head(wt) == head(main)
    assert workspace_prep.project_profile("other-app", tmp_path) == {}


def test_a_cards_own_pr_base_wins_over_the_projects(tmp_path, monkeypatch):
    # the user said "branch out from IC-develop" on a project whose base is develop: no reset onto develop
    main, wt = profile_on_develop(tmp_path, monkeypatch, body="REQUEST...\n\nPR BASE: `ic` (start from origin/ic)")
    git(main, "update-ref", "refs/remotes/origin/ic", "HEAD~1")
    head = lambda d: subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    before = head(wt)
    note = workspace_prep.prepare_workspace(is_first_turn=True)["context"]
    assert "origin/develop" not in note and "PRs target `ic`" in note
    assert head(wt) == before  # already on origin/ic


def test_worktree_with_work_in_it_is_never_reset(tmp_path, monkeypatch):
    main, wt = profile_on_develop(tmp_path, monkeypatch)
    (wt / "draft.txt").write_text("uncommitted")
    git(wt, "add", "draft.txt")
    note = workspace_prep.prepare_workspace(is_first_turn=True)["context"]
    assert "not based on origin/develop" in note and "reset" not in note
    assert (wt / "draft.txt").read_text() == "uncommitted"


def test_worktree_gets_a_clone_of_the_code_index(tmp_path, monkeypatch):
    main, wt = repo_with_worktree(tmp_path)
    (main / ".codegraph").mkdir()
    (main / ".codegraph" / "codegraph.db").write_text("index")
    calls = []
    monkeypatch.setattr(workspace_prep.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(workspace_prep, "_sync_codegraph", calls.append)
    assert workspace_prep.link_codegraph(wt)
    assert (wt / ".codegraph" / "codegraph.db").read_text() == "index"
    assert str(wt) in (wt / ".codegraph" / "source.json").read_text()
    assert calls == [wt]
    status = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True)
    assert ".codegraph" not in status.stdout
    assert not workspace_prep.link_codegraph(wt)      # already there
    assert not workspace_prep.link_codegraph(main)    # the main checkout keeps its own


def test_inline_scripts_note_follows_the_approval_config(tmp_path):
    assert workspace_prep.inline_scripts_refused(tmp_path)  # no config: Hermes defaults to deny
    (tmp_path / "config.yaml").write_text("approvals:\n  single_query_mode: allow\n")
    assert not workspace_prep.inline_scripts_refused(tmp_path)
