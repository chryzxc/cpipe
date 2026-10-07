"""Shipping closes test/refactor gaps before publication, using real Git and checks.

Specialist verdicts are scripted: this does not claim live-model performance.
"""
import subprocess

from cpipe import engines
from cpipe.engines import hermes
from cpipe.round import head
from test_stage_commands import existing, git
from test_stage_gates import frozen_runner


def test_ship_adds_missing_tests_and_scoped_refactor_before_publication(existing, tmp_path, monkeypatch):
    # Existing behavior is correct but duplicated and untested. No retrospective RED.
    (existing / "code.py").write_text("def first():\n    return 2\n\ndef second():\n    return 2\n")
    git(existing, "commit", "-qam", "feat: add matching accessors")
    source = head(existing)
    r = frozen_runner(tmp_path, monkeypatch, existing)
    r.state.update(node="setup", entry="ship", publish=True, ready=False, adopted=True,
                   adoption={"head": source, "base_head": git(existing, "rev-parse", "main")})
    monkeypatch.setattr("cpipe.round.hermes_api.comments_after", lambda *a: [])
    monkeypatch.setattr("cpipe.round.workspace_prep.project_profile", lambda *a: {"base_branch": "main", "qa": "off"})
    monkeypatch.setattr("cpipe.round.quality.load", lambda *a: {"checks": [
        {"name": "regression", "run": "python -B -c \"import runpy, unittest; c = runpy.run_path('code.py'); assert c['first']() == c['second']() == 2; assert unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('.')).wasSuccessful()\""}]})
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    calls, published = [], []

    def run(ec, prompt, cwd, **kw):
        role = ec["profile"]
        calls.append(role)
        if role == "sentry":
            assert kw["readonly"] is True
            assert "Completion review" in prompt
            assert "integration boundaries" in prompt
            assert "behavior-preserving refactors" in prompt
            assert "regression-suite evidence" in prompt
            assert "checks_receipt" in prompt and head(existing) in prompt
            if not (existing / "test_accessors.py").exists():
                assert not published
                return engines.EngineResult("changes", session="review-session", data={"findings": [
                    "REV-001: add characterization tests for both accessors",
                    "REV-002: share the accessor implementation without changing behavior"]})
            assert "REV-001" in prompt and "REV-002" in prompt
            assert f"{source}..{head(existing)}" in prompt
            return engines.EngineResult("approve", session="review-session")
        assert role == "forge"
        assert "regression/characterization tests before editing code" in prompt
        assert "may pass immediately" in prompt and "Do not claim retrospective TDD" in prompt
        assert "do not weaken tests or expand scope" in prompt
        assert not published
        (existing / "test_accessors.py").write_text(
            "import runpy\nimport unittest\n\n"
            "class Accessors(unittest.TestCase):\n"
            "    def test_preserved_values(self):\n"
            "        code = runpy.run_path('code.py')\n"
            "        self.assertEqual(code['first'](), 2)\n"
            "        self.assertEqual(code['second'](), 2)\n")
        cmd = ["python", "-B", "-m", "unittest", "discover", "-q"]
        assert subprocess.run(cmd, cwd=cwd, capture_output=True).returncode == 0
        (existing / "code.py").write_text("def first():\n    return 2\n\nsecond = first\n")
        assert subprocess.run(cmd, cwd=cwd, capture_output=True).returncode == 0
        git(existing, "add", "code.py", "test_accessors.py")
        git(existing, "commit", "-qm", "refactor: share accessors with characterization coverage")
        return engines.EngineResult("done", session="builder-session", data={"summary": "Preserve both accessor values"})

    def publish(ws, state, branch):
        assert state["findings"] == []
        assert state["checks_receipt"] == state["verification_receipt"] == state["review_receipt"]
        assert state["verified_head"] == head(ws) != source
        assert state["checks_evidence"][0]["status"] == "pass"
        published.append(head(ws))
        return "ok", "local publication seam"

    monkeypatch.setattr(hermes, "run", run)
    monkeypatch.setattr("cpipe.round.pr.publish", publish)
    result = r.run()
    assert result["status"] == "needs_review" and r.state["node"] == "handoff"
    assert calls == ["sentry", "forge", "sentry"]
    assert published == [head(existing)]
    assert not r.state.get("tdd")


def test_contract_upgrade_invalidates_old_approval(existing, tmp_path, monkeypatch):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    key = r.gate_key()
    r.state.update(checks_receipt=key, verification_receipt=key, review_receipt=key)
    monkeypatch.setattr("cpipe.round.REVIEW_CONTRACT_VERSION", 2)
    assert r.gate_key() != key
    assert r.final("", "")[0] == "reverify"


def test_content_scope_does_not_require_test_or_refactor_work(existing, tmp_path, monkeypatch):
    r = frozen_runner(tmp_path, monkeypatch, existing)
    r.state["size"] = "content"
    prompts = []
    monkeypatch.setattr(engines, "available", lambda *a: (True, ""))
    monkeypatch.setattr(r, "call", lambda node, name, eng, ec, prompt, **kw:
                        prompts.append(prompt) or engines.EngineResult("approve"))
    assert r.review()[0] is True
    assert "Content-only scope" in prompts[0] and "Completion review" not in prompts[0]
    r.state.update(entry="fix", rounds={"review": 1})
    monkeypatch.setattr(r, "turn", lambda node, prompt:
                        prompts.append(prompt) or engines.EngineResult("done"))
    assert r.build("Correct the wording", "")[0] == "ok"
    assert "characterization tests" not in prompts[-1]
