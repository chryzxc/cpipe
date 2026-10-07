"""The setup node: a fresh worktree on the PR base, deps and the code index linked from the main checkout."""
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


def head(d):
    return subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout


def test_setup_links_missing_node_modules_once(tmp_path, monkeypatch):
    main, wt = repo_with_worktree(tmp_path)
    monkeypatch.setattr(workspace_prep.shutil, "which", lambda cmd: None)  # no codegraph
    notes = workspace_prep.prepare(wt, None)
    assert any("symlinked" in n and "client" in n for n in notes)
    assert (wt / "node_modules").resolve() == (main / "node_modules").resolve()
    status = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True)
    assert "node_modules" not in status.stdout
    assert workspace_prep.prepare(wt, None) == []  # nothing left to do
    assert workspace_prep.prepare(main, None) == []  # the main checkout keeps its own


def on_develop(tmp_path):
    main, wt = repo_with_worktree(tmp_path)
    git(main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "on develop only")
    git(main, "update-ref", "refs/remotes/origin/develop", "HEAD")  # origin/develop is ahead of wt's base
    return main, wt


def test_fresh_worktree_is_reset_onto_the_pr_base(tmp_path):
    main, wt = on_develop(tmp_path)
    assert any("reset onto origin/develop" in n for n in workspace_prep.prepare(wt, "develop"))
    assert head(wt) == head(main)


def test_worktree_with_work_in_it_is_never_reset(tmp_path):
    main, wt = on_develop(tmp_path)
    (wt / "draft.txt").write_text("uncommitted")
    git(wt, "add", "draft.txt")
    assert not any("reset" in n for n in workspace_prep.prepare(wt, "develop"))
    assert (wt / "draft.txt").read_text() == "uncommitted"


def test_project_profile_reads_projects_yaml(tmp_path):
    (tmp_path / "delivery").mkdir()
    (tmp_path / "delivery" / "projects.yaml").write_text("projects:\n  app:\n    base_branch: develop\n")
    assert workspace_prep.project_profile("app", tmp_path) == {"base_branch": "develop"}
    assert workspace_prep.project_profile("other", tmp_path) == {}
    assert workspace_prep.project_profile("app", tmp_path / "unset") == {}


def test_worktree_gets_a_clone_of_the_code_index(tmp_path, monkeypatch):
    main, wt = repo_with_worktree(tmp_path)
    (main / ".codegraph").mkdir()
    (main / ".codegraph" / "codegraph.db").write_text("index")
    calls = []
    run = subprocess.run

    def clone_or_run(args, **kwargs):
        if args[0] == "cp":
            # The success path must not require CI's filesystem to support
            # copy-on-write (the production path still refuses full copies).
            import sys
            flag = "-c" if sys.platform == "darwin" else "--reflink=always"
            assert args == ["cp", flag, str(main / ".codegraph" / "codegraph.db"),
                            str(wt / ".codegraph")]
            workspace_prep.shutil.copyfile(args[2], wt / ".codegraph" / "codegraph.db")
            return subprocess.CompletedProcess(args, 0)
        return run(args, **kwargs)

    monkeypatch.setattr(workspace_prep.subprocess, "run", clone_or_run)
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


def test_code_index_clone_failure_leaves_no_copy(tmp_path, monkeypatch):
    main, wt = repo_with_worktree(tmp_path)
    (main / ".codegraph").mkdir()
    (main / ".codegraph" / "codegraph.db").write_text("index")
    run = subprocess.run
    attempts, synced = [], []

    def failed_clone_or_run(args, **kwargs):
        if args[0] == "cp":
            attempts.append(args)
            return subprocess.CompletedProcess(args, 1)
        return run(args, **kwargs)

    monkeypatch.setattr(workspace_prep.subprocess, "run", failed_clone_or_run)
    monkeypatch.setattr(workspace_prep.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(workspace_prep, "_sync_codegraph", synced.append)
    assert not workspace_prep.link_codegraph(wt)
    assert len(attempts) == 1  # no fallback to an expensive full copy
    assert not (wt / ".codegraph").exists()
    assert synced == []
