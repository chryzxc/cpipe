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
    assert module["retention_hours"]("SESSION_CLEANUP_CRON_RETENTION_HOURS", 48) == 48

    monkeypatch.setenv("SESSION_CLEANUP_CRON_RETENTION_HOURS", "6")
    assert module["retention_hours"]("SESSION_CLEANUP_CRON_RETENTION_HOURS", 48) == 6

    for bad in ("nope", "0", "-3"):
        monkeypatch.setenv("SESSION_CLEANUP_CRON_RETENTION_HOURS", bad)
        assert module["retention_hours"]("SESSION_CLEANUP_CRON_RETENTION_HOURS", 48) == 48


def test_prune_args_target_only_automation_sessions():
    module = load_module()

    real = module["prune_args"](48, ["--source", "cron"], yes=True)
    assert real == ["sessions", "prune", "--older-than", "48h",
                    "--source", "cron", "--yes"]

    preview = module["prune_args"](168, ["--source", "cli", "--title",
                                         "Kanban supervisor wake:"], yes=False)
    assert preview == ["sessions", "prune", "--older-than", "168h",
                       "--source", "cli", "--title", "Kanban supervisor wake:",
                       "--dry-run"]

    for args in (real, preview):
        assert "--include-pinned" not in args
        assert "--include-archived" not in args


def test_run_prune_reports_cli_summary_and_failures(tmp_path):
    module = load_module()

    stub = tmp_path / "hermes-stub"
    stub.write_text("#!/bin/sh\necho 'Deleted 12 sessions (freed 3.4 MB)'\n")
    stub.chmod(0o755)
    ok = module["run_prune"](stub, 48, ["--source", "cron"], yes=True)
    assert ok == "--source cron: Deleted 12 sessions (freed 3.4 MB)"

    failing = tmp_path / "hermes-fail"
    failing.write_text("#!/bin/sh\necho 'database is locked' >&2\nexit 1\n")
    failing.chmod(0o755)
    bad = module["run_prune"](failing, 48, ["--source", "cron"], yes=True)
    assert bad == "--source cron: prune failed (database is locked)"

    missing = tmp_path / "hermes-missing"
    gone = module["run_prune"](missing, 48, ["--source", "cron"], yes=True)
    assert gone.startswith("--source cron: prune failed (") or "prune failed" in gone


def test_main_without_binary_reports_and_writes_digest(tmp_path, monkeypatch, capsys):
    home = tmp_path / "hermes"
    (home / "logs").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    module = runpy.run_path(str(CLEANUP_PATH))
    module["main"]()

    out = capsys.readouterr().out
    assert "hermes binary not found" in out
    digest = json.loads("null") if False else (home / "logs" / "session-hygiene-digest.md").read_text()
    assert "# Session hygiene digest" in digest
    assert "hermes binary not found" in digest


def test_cleanup_job_registered_in_cron_definitions():
    defs = json.loads((ROOT / "workflow" / "cron.jobs.json").read_text())
    job = next(j for j in defs if j["name"] == "Session hygiene daily")
    assert job["script"] == "session_cleanup.py"
    assert job["no_agent"] is True
    assert job["enabled"] is True
    assert job["schedule"]["minutes"] == 1440
