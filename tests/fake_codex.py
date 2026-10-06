#!/usr/bin/env python3
"""A scripted stand-in for ``codex exec [resume <thread>] --json -o F ... <prompt>`` on the python fixture.

    first turn            -> a wrong fix (add multiplies), committed: the tests check fails
    "checks failed"       -> the right fix, committed
    "New input"           -> a commit that addresses the feedback
    a review prompt       -> approve
    "Tests first"         -> a failing test committed (FAKE_TDD=weak: one that passes, the first time)
    a plan prompt         -> a plan and a question (none once revised); two parts with FAKE_PLAN=split
    FAKE_CODEX=rate_limit -> an error event and no result (only when run as fake_codex.py: a symlink to this
                             file under another name is a healthy fallback engine)

Every call is appended to $FAKE_CODEX_LOG as {"bin", "thread", "resumed", "readonly", "prompt"}.
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

args = sys.argv[1:]
resumed = args[1] == "resume"
thread = args[2] if resumed else f"thread-{uuid.uuid4().hex[:8]}"
out_file, prompt = Path(args[args.index("-o") + 1]), args[-1]
cwd = Path(args[args.index("-C") + 1]) if "-C" in args else Path.cwd()
name = Path(sys.argv[0]).name
with open(os.environ["FAKE_CODEX_LOG"], "a") as f:
    f.write(json.dumps({"bin": name, "thread": thread, "resumed": resumed,
                        "readonly": 'sandbox_mode="read-only"' in args, "prompt": prompt}) + "\n")


def emit(**ev):
    print(json.dumps(ev), flush=True)


def commit(code: str, msg: str):
    (cwd / "calc.py").write_text(code)
    git = ["git", "-c", "user.email=fake@codex", "-c", "user.name=fake"]
    subprocess.run([*git, "add", "calc.py"], cwd=cwd, check=True)
    subprocess.run([*git, "commit", "-qm", msg], cwd=cwd, check=True)


def result(**data):
    emit(type="item.completed", item={"type": "agent_message", "text": "done"})
    emit(type="turn.completed", usage={"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 50})
    out_file.write_text(json.dumps(data))
    sys.exit(0)


if not resumed:
    emit(type="thread.started", thread_id=thread)
emit(type="turn.started")
if os.environ.get("FAKE_CODEX") == "rate_limit" and name == "fake_codex.py":
    emit(type="error", message="You've hit your usage limit. Try again later.")
    sys.exit(1)
if "You review a change" in prompt or "Review it again" in prompt:
    result(status="approve", summary="Looks right.", findings=[])
if "Before building, plan" in prompt:
    parts = [{"title": f"Part {i}", "request": f"{prompt.splitlines()[0]} (part {i})", "after": []} for i in (1, 2)]
    revised = "answers and changes to the plan" in prompt
    result(status="done", summary="Planned.", question="", plan="1. Make add add.\n2. Test it." + (" (revised)" if revised else ""),
           questions=[] if revised else ["Should add accept floats? Recommended: yes."],
           parts=parts if os.environ.get("FAKE_PLAN") == "split" else [])
if "Tests first, before any code change" in prompt:
    weak = os.environ.get("FAKE_TDD") == "weak" and "already pass" not in prompt
    (cwd / "test_more.py").write_text("import unittest\n\nfrom calc import add\n\n\nclass MoreTest(unittest.TestCase):\n"
                                      "    def test_add_ones(self):\n        self.assertEqual(add(1, 1), 2)\n")
    git = ["git", "-c", "user.email=fake@codex", "-c", "user.name=fake"]
    subprocess.run([*git, "add", "test_more.py"], cwd=cwd, check=True)
    subprocess.run([*git, "commit", "-qm", "test: add adds", "--allow-empty"], cwd=cwd, check=True)
    result(status="done", summary="Tests first.", question="", kind="fix", tests=["test_add_ones"],
           guard_command="python3 -m unittest test_calc.CalcTest.test_mul" if (cwd / "test_calc.py").exists() else "",
           red_command="true" if weak else "python3 -m unittest test_more")
if "New input" in prompt:
    commit((cwd / "calc.py").read_text() + "\n\n# feedback addressed\n", "chore: address feedback")
elif "checks failed" in prompt:
    commit("def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n", "fix: add adds")
else:
    commit("def add(a, b):\n    return a * b\n\n\ndef mul(a, b):\n    return a * b\n", "fix: add")
result(status="done", summary="Fixed add.", assumptions=["a + b"], question="", how_to_test="Open /?a=2&b=3")
