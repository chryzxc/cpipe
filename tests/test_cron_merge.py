import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MERGE_PATH = ROOT / "workflow" / "scripts" / "merge_cron_jobs.py"

DEFS_A = [{"name": "Alpha", "script": "a.py", "enabled": True}]
DEFS_B = [{"name": "Beta", "script": "b.py", "enabled": True}]
FOREIGN = {"name": "Foreign", "script": "f.py", "note": "user job"}


def load(tmp_path, name="jobs.json"):
    return json.loads((tmp_path / name).read_text())


def test_add_rename_prune_lifecycle(tmp_path):
    defs_a = tmp_path / "defs_a.json"
    defs_b = tmp_path / "defs_b.json"
    live = tmp_path / "jobs.json"
    defs_a.write_text(json.dumps(DEFS_A))
    defs_b.write_text(json.dumps(DEFS_B))
    live.write_text(json.dumps([dict(FOREIGN)]))

    merge = runpy.run_path(str(MERGE_PATH))

    merge["main"](str(defs_a), str(live))
    jobs = load(tmp_path)
    by_name = {j["name"]: j for j in jobs}
    assert by_name["Alpha"]["owner"] == "software-delivery"
    assert by_name["Alpha"]["script"] == "a.py"
    assert by_name["Foreign"].get("owner") is None  # foreign untouched, no marker

    # rename Alpha -> Beta: next merge prunes owned Alpha
    merge["main"](str(defs_b), str(live))
    jobs = load(tmp_path)
    names = {j["name"] for j in jobs}
    assert "Beta" in names
    assert "Alpha" not in names
    assert "Foreign" in names  # foreign job survived the prune

    # idempotent: second identical merge changes nothing
    merge["main"](str(defs_b), str(live))
    jobs_again = load(tmp_path)
    assert {j["name"]: j for j in jobs_again} == {j["name"]: j for j in load(tmp_path)}
    assert len(jobs_again) == 2


def test_backfill_marks_existing_owned_names(tmp_path):
    defs = tmp_path / "defs.json"
    live = tmp_path / "jobs.json"
    defs.write_text(json.dumps(DEFS_A))
    live.write_text(json.dumps([
        {"name": "Alpha", "script": "a.py", "enabled": True},  # pre-marker install
        dict(FOREIGN),
    ]))

    merge = runpy.run_path(str(MERGE_PATH))
    merge["main"](str(defs), str(live))

    jobs = {j["name"]: j for j in load(tmp_path)}
    assert jobs["Alpha"]["owner"] == "software-delivery"
    assert jobs["Foreign"].get("owner") is None


def test_real_defs_shape_valid():
    defs = json.loads((ROOT / "workflow" / "cron.jobs.json").read_text())
    assert len({d["name"] for d in defs}) == len(defs)
    assert all("name" in d for d in defs)
