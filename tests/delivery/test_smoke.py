"""Real offline v2 CLI exercise, not a model or native worker simulation."""
import json
import sys

import pytest

from cpipe.runtime.cli import main


@pytest.mark.skipif(sys.platform != "darwin", reason="offline smoke requires macOS sandbox-exec")
def test_smoke_cli_runs_real_commands_and_persists_testable_candidate(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    before = set(tmp_path.iterdir())
    assert main(["v2-smoke"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "offline_fixture"
    assert report["phase"] == "testable"
    assert report["live_workers_enabled"] is False
    assert report["publication_enabled"] is False
    assert report["native_tasks_created"] == 0
    assert report["model_calls"] == 0
    assert report["review_performed"] is False
    assert report["journal_reopened"] is True
    assert report["event_replay_verified"] is True
    assert report["red_exit_code"] == 1
    assert report["green_exit_code"] == 0
    assert report["outside_write_denied"] is True
    assert report["git_write_denied"] is True
    assert report["network_denied"] is True
    assert report["artifact_before"] != report["artifact_after"]
    assert report["scratch_cleaned"] is True
    assert set(tmp_path.iterdir()) == before


def test_smoke_cli_reports_unavailable_boundary_without_unconfined_fallback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    before = set(tmp_path.iterdir())
    monkeypatch.setattr("cpipe.delivery.sandbox.shutil.which", lambda _: None)
    assert main(["v2-smoke"]) == 1
    assert "unavailable" in capsys.readouterr().err
    assert set(tmp_path.iterdir()) == before
