"""skills/run/bin/cpipe.py over recorded runner output: every failing name ends up new, baseline, flaky or to rerun."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("facts", Path(__file__).parents[1] / "skills/run/bin/cpipe.py")
facts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(facts)

PYTEST_JUNIT = """<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" errors="0" failures="2"
 skipped="1" tests="5"><testcase classname="tests.test_orders" name="test_total" time="0.01"/>
<testcase classname="tests.test_orders" name="test_filter" time="0.02"><failure message="assert 2 == 3">E   assert 2 == 3
</failure></testcase><testcase classname="tests.test_orders" name="test_slow" time="0"><skipped message="slow"/></testcase>
<testcase classname="tests.test_users" name="test_login" time="0.1"><error message="fixture db">db down</error></testcase>
<testcase classname="tests.test_users" name="test_logout" time="0.1"/></testsuite></testsuites>"""

JEST = {"numFailedTests": 1, "success": False, "testResults": [
    {"name": "/work/base/src/orders.test.ts", "status": "failed", "assertionResults": [
        {"ancestorTitles": ["orders", "filter"], "title": "by date", "status": "failed"},
        {"ancestorTitles": ["orders"], "title": "totals", "status": "passed"},
        {"ancestorTitles": ["orders"], "title": "later", "status": "pending"}]},
    {"name": "/work/base/src/broken.test.ts", "status": "failed", "assertionResults": [],
     "message": "Cannot find module"}]}

TSC = """src/a.ts(10,5): error TS2322: Type 'string' is not assignable to type 'number'.
src/b.ts:3:1 - error TS2304: Cannot find name 'foo'.
Found 2 errors."""

ESLINT = [{"filePath": "/work/head/src/a.ts", "messages": [
    {"ruleId": "no-unused-vars", "severity": 2, "message": "'x' is defined but never used.", "line": 4},
    {"ruleId": "semi", "severity": 1, "message": "Missing semicolon.", "line": 9}]}]


def write(path, text):
    path.write_text(text if isinstance(text, str) else json.dumps(text))
    return str(path)


def test_parsers_name_each_result_without_tree_paths(tmp_path):
    r = facts.results(tmp_path / "o.json", "junit", [write(tmp_path / "j.xml", PYTEST_JUNIT)], "/x")
    assert r["failed"] == ["tests.test_orders::test_filter", "tests.test_users::test_login"]
    assert r["skipped"] == ["tests.test_orders::test_slow"] and len(r["passed"]) == 2
    r = facts.results(tmp_path / "o.json", "jest", [write(tmp_path / "j.json", JEST)], "/work/base")
    assert r["failed"] == ["src/orders.test.ts::orders filter by date"]
    assert r["errors"] == ["src/broken.test.ts::<suite failed to run>"] and r["skipped"] == ["src/orders.test.ts::orders later"]
    r = facts.results(tmp_path / "o.json", "tsc", [write(tmp_path / "t.txt", TSC)], "/x")
    assert r["kind"] == "static" and r["errors"] == [
        "src/a.ts:TS2322:Type 'string' is not assignable to type 'number'.", "src/b.ts:TS2304:Cannot find name 'foo'."]
    r = facts.results(tmp_path / "o.json", "eslint", [write(tmp_path / "e.json", ESLINT)], "/work/head")
    assert r["errors"] == ["src/a.ts:no-unused-vars:'x' is defined but never used."]
    r = facts.results(tmp_path / "o.json", "lines", [write(tmp_path / "l.txt", "/r/a.py:12:4: E501 line too long\n")], "/r")
    assert r["errors"] == ["a.py: E501 line too long"]


def test_cut_off_or_missing_results_ask_for_a_rerun(tmp_path):
    with pytest.raises(SystemExit, match="cut off"):
        facts.results(tmp_path / "o.json", "junit", [write(tmp_path / "j.xml", PYTEST_JUNIT[:300])], "/x")
    with pytest.raises(SystemExit, match="no results file"):
        facts.results(tmp_path / "o.json", "jest", [str(tmp_path / "missing.json")], "/x")


def put(run, name, **kw):
    (run / "results").mkdir(parents=True, exist_ok=True)
    rec = {"kind": "tests", "passed": [], "failed": [], "skipped": [], "errors": [], **kw}
    (run / "results" / name).write_text(json.dumps(rec))


def closed(run):
    facts.track(run, "set", [facts.track(run, "add", ["verify", "related", "tests", "pass"]), "done", "41 pass"])


def test_missing_state_never_passes(tmp_path):
    """A run from before the checklist, or one that lost it, has proven nothing."""
    assert "no checklist" in facts.gate(tmp_path) and "no check results" in facts.gate(tmp_path)
    assert facts.report(tmp_path).startswith("not ready to report")
    closed(tmp_path)
    put(tmp_path, "unit.base.json", passed=["a"])  # saved on the base, never run on the delivery
    assert facts.gate(tmp_path).startswith("FIX: unit: run * alone")


def test_seven_failures_on_both_trees_are_baseline_and_pass_the_gate(tmp_path):
    failing = [f"t{i}" for i in range(7)]
    put(tmp_path, "unit.base.json", failed=failing, passed=["a"])
    put(tmp_path, "unit.head.json", failed=failing, passed=["a", "b"])
    closed(tmp_path)
    c = facts.compare(tmp_path, "unit")
    assert c["baseline"] == failing and not (c["new"] or c["flaky"] or c["rerun-head"] or c["rerun-base"])
    assert facts.gate(tmp_path).startswith("PASS")


def test_a_new_name_is_rerun_alone_then_decided(tmp_path):
    put(tmp_path, "unit.base.json", passed=["a", "b"])
    put(tmp_path, "unit.head.json", failed=["a", "b", "c"])
    c = facts.compare(tmp_path, "unit")
    assert c["rerun-head"] == ["a", "b", "c"] and facts.gate(tmp_path).startswith("FIX")
    put(tmp_path, "unit.head-alone.json", passed=["a"], failed=["b", "c"])
    c = facts.compare(tmp_path, "unit")
    assert c["flaky"] == ["a"] and c["new"] == ["b"] and c["rerun-base"] == ["c"]
    put(tmp_path, "unit.base-alone.json", failed=["c"])
    c = facts.compare(tmp_path, "unit")
    assert c["baseline"] == ["c"] and c["new"] == ["b"] and "new failure b" in facts.gate(tmp_path)


def test_static_errors_compare_by_id_without_reruns(tmp_path):
    put(tmp_path, "types.base.json", kind="static", errors=["a.ts:TS1:x"])
    put(tmp_path, "types.head.json", kind="static", errors=["a.ts:TS1:x", "b.ts:TS2:y"])
    c = facts.compare(tmp_path, "types")
    assert c["baseline"] == ["a.ts:TS1:x"] and c["new"] == ["b.ts:TS2:y"]


def test_track_needs_evidence_and_the_gate_orders_its_verdicts(tmp_path):
    a = facts.track(tmp_path, "add", ["verify", "related", "tests", "pass"])
    b = facts.track(tmp_path, "add", ["report", "merge", "approved"])
    assert facts.track(tmp_path, "add", ["verify", "related", "tests", "pass"]) == a
    with pytest.raises(SystemExit, match="evidence"):
        facts.track(tmp_path, "set", [a, "done"])
    assert facts.gate(tmp_path).startswith("FIX") and a in facts.track(tmp_path, "open", [])
    facts.track(tmp_path, "set", [a, "done", "41", "pass"])
    put(tmp_path, "unit.base.json", passed=["a"])
    put(tmp_path, "unit.head.json", passed=["a"])
    facts.track(tmp_path, "set", [b, "user", "a required approving review"])
    (tmp_path / "results" / "ci.json").write_text(json.dumps([{"name": "test", "bucket": "pending"}]))
    assert facts.gate(tmp_path) == "WAIT: CI test"
    (tmp_path / "results" / "ci.json").write_text(json.dumps([{"name": "test", "bucket": "pass"}]))
    assert facts.gate(tmp_path) == f"NEEDS-USER: {b} a required approving review"
    assert facts.report(tmp_path).startswith("⚠️ Done except")


def test_the_report_waits_for_the_loop(tmp_path):
    closed(tmp_path)
    put(tmp_path, "unit.base.json", passed=["a"])
    put(tmp_path, "unit.head.json", failed=["a"])
    put(tmp_path, "unit.head-alone.json", failed=["a"])
    assert facts.report(tmp_path).startswith("not ready to report")
    put(tmp_path, "unit.head.json", passed=["a"])
    out = facts.report(tmp_path)
    assert out.startswith("✅ Done") and "✅ unit: 1 pass, 0 baseline, 0 new" in out


def test_active_registers_this_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "p"))
    monkeypatch.setenv("HERMES_SESSION_ID", "s1")
    facts.active(tmp_path / "run")
    assert json.loads((tmp_path / "cpipe/active/s1.json").read_text())["run_dir"] == str((tmp_path / "run").resolve())
    facts.active(tmp_path / "run", done=True)
    assert not (tmp_path / "cpipe/active/s1.json").exists()


def test_chat_flags_follow_this_chats_profile_model_and_switched_provider(tmp_path, monkeypatch):
    db = sqlite3.connect(tmp_path / "state.db")
    db.execute("CREATE TABLE sessions (id TEXT, profile_name TEXT, model TEXT, billing_provider TEXT, "
               "model_config TEXT)")
    db.execute("INSERT INTO sessions VALUES ('s1', 'builder', 'model-a', 'prov-a', NULL)")
    db.execute("INSERT INTO sessions VALUES ('s2', 'builder', 'model-b', 'prov-a', '{\"provider\": \"prov-b\"}')")
    db.commit()
    db.close()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SESSION_ID", "s1")
    assert facts.chat() == "-p builder -m model-a --provider prov-a"
    monkeypatch.setenv("HERMES_SESSION_ID", "s2")
    assert facts.chat() == "-p builder -m model-b --provider prov-b"


def test_a_whole_loop_through_the_command_line(tmp_path):
    """'Merge it if there are no regressions': failures on both trees are baseline, a new one loops, then PASS."""
    import subprocess
    import sys

    def cli(*args):
        out = subprocess.run([sys.executable, spec.origin, *map(str, args)], capture_output=True, text=True)
        return (out.stdout + out.stderr).strip()

    def junit(name, failing):
        cases = "".join(f'<testcase classname="t" name="{n}">' + ('<failure message="x"/>' if n in failing else "")
                        + "</testcase>" for n in ["old", "new", "ok"])
        return write(tmp_path / name, f"<testsuites><testsuite>{cases}</testsuite></testsuites>")

    run, res = tmp_path / "run", tmp_path / "run" / "results"
    res.mkdir(parents=True)
    item = cli("track", run, "add", "report", "merge it if there are no regressions").split()[-1]
    cli("results", res / "unit.base.json", "junit", junit("base.xml", {"old"}))
    cli("results", res / "unit.head.json", "junit", junit("head.xml", {"old", "new"}))
    assert "rerun-head: t::new" in cli("compare", run, "unit") and cli("gate", run).startswith("FIX")
    cli("results", res / "unit.head-alone.json", "junit", junit("alone.xml", {"new"}))
    assert "new: t::new" in cli("compare", run, "unit") and "new failure t::new" in cli("gate", run)
    cli("results", res / "unit.head.json", "junit", junit("fixed.xml", {"old"}))  # fixed at its cause
    for stale in res.glob("unit.head-alone*.json"):
        stale.unlink()
    assert "baseline: t::old" in cli("compare", run, "unit") and cli("gate", run) == f"FIX: {item} merge it if there are no regressions"
    cli("track", run, "set", item, "done", "no new failures; merged")
    assert cli("gate", run).startswith("PASS") and cli("report", run).startswith("✅ Done")
