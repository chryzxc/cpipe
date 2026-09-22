import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEANUP_PATH = ROOT / "workflow" / "scripts" / "session_cleanup.py"


def load_module():
    return runpy.run_path(str(CLEANUP_PATH))


def test_retention_hours_defaults_and_env_override(monkeypatch):
    module = load_module()

    monkeypatch.delenv("SESSION_CLEANUP_CRON_RETENTION_HOURS", raising=False)
    assert module["retention_hours"]("SESSION_CLEANUP_CRON_RETENTION_HOURS", 6) == 6

    monkeypatch.setenv("SESSION_CLEANUP_CRON_RETENTION_HOURS", "12")
    assert module["retention_hours"]("SESSION_CLEANUP_CRON_RETENTION_HOURS", 6) == 12

    for bad in ("nope", "0", "-3"):
        monkeypatch.setenv("SESSION_CLEANUP_CRON_RETENTION_HOURS", bad)
        assert module["retention_hours"]("SESSION_CLEANUP_CRON_RETENTION_HOURS", 6) == 6


def test_archive_args_are_soft_hide_only():
    module = load_module()

    args = module["archive_args"](6, ["--source", "cron"], yes=True)
    assert args == ["sessions", "archive", "--older-than", "6h",
                    "--source", "cron", "--yes"]

    preview = module["archive_args"](168, ["--source", "cli", "--title",
                                           "Kanban supervisor wake:"], yes=False)
    assert preview[-1] == "--dry-run"

    for built in (args, preview):
        assert "prune" not in built
        assert "--include-pinned" not in built


def test_prune_args_reclaim_archived_but_never_force():
    module = load_module()

    args = module["prune_args"](720, ["--source", "cron"], yes=True)
    assert args == ["sessions", "prune", "--older-than", "720h",
                    "--include-archived", "--source", "cron", "--yes"]
    assert "--force" not in args
    assert "--include-pinned" not in args


def test_run_engine_reports_live_gateway_refusal_without_failing(tmp_path):
    module = load_module()

    refuser = tmp_path / "hermes-refuse"
    refuser.write_text(
        "#!/bin/sh\n"
        "echo 'Refusing `hermes sessions prune`: another process is using state.db.' >&2\n"
        "exit 1\n")
    refuser.chmod(0o755)

    outcome, lines = module["run_engine"](refuser, ["sessions", "prune"])
    assert outcome.startswith("skipped — gateway holds state.db")
    assert lines == []


def test_run_engine_returns_cli_summary(tmp_path):
    module = load_module()

    stub = tmp_path / "hermes-stub"
    stub.write_text("#!/bin/sh\necho 'Archived 12 session(s). Hidden from listings.'\n")
    stub.chmod(0o755)

    outcome, lines = module["run_engine"](stub, ["sessions", "archive", "--yes"])
    assert outcome == "Archived 12 session(s). Hidden from listings."
    assert module["matched_count"](lines) == "Archived 12 session(s). Hidden from listings."


def test_main_archive_only_by_default_and_writes_digest(tmp_path, monkeypatch, capsys):
    home = tmp_path / "hermes"
    (home / "logs").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("sys.argv", ["session_cleanup.py"])

    module = runpy.run_path(str(CLEANUP_PATH))
    module["main"]()

    out = capsys.readouterr().out
    assert "hermes binary not found" in out
    digest = (home / "logs" / "session-hygiene-digest.md").read_text()
    assert "# Session hygiene digest" in digest
    assert "prune automation sessions" not in out


def test_main_prune_flag_adds_deletion_pass(tmp_path, monkeypatch, capsys):
    home = tmp_path / "hermes"
    (home / "logs").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("sys.argv", ["session_cleanup.py", "--prune"])

    module = runpy.run_path(str(CLEANUP_PATH))
    module["main"]()

    out = capsys.readouterr().out
    assert "hermes binary not found" in out


def test_cleanup_job_registered_in_cron_definitions():
    defs = json.loads((ROOT / "workflow" / "cron.jobs.json").read_text())
    job = next(j for j in defs if j["name"] == "Session hygiene daily")
    assert job["script"] == "session_cleanup.py"
    assert job["no_agent"] is True
    assert job["enabled"] is True
    assert job["schedule"]["minutes"] == 1440
