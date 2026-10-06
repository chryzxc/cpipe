#!/usr/bin/env python3
"""A scripted stand-in for ``codex exec [resume <thread>] --json -o F ... <prompt>`` on the python fixture.

    first turn            -> a wrong fix (add multiplies), committed: the tests check fails
    "checks failed"       -> the right fix, committed
    "New input"           -> a commit that addresses the feedback
    a review prompt       -> approve
    a plan prompt         -> no parts, or two independent parts with FAKE_PLAN=split
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
    subprocess.run([*git, "commit", "-qam", msg], cwd=cwd, check=True)


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
    result(status="done", summary="Planned.", question="", parts=parts if os.environ.get("FAKE_PLAN") == "split" else [])
if "New input" in prompt:
    commit((cwd / "calc.py").read_text() + "\n\n# feedback addressed\n", "chore: address feedback")
elif "checks failed" in prompt:
    commit("def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n", "fix: add adds")
else:
    commit("def add(a, b):\n    return a * b\n\n\ndef mul(a, b):\n    return a * b\n", "fix: add")
result(status="done", summary="Fixed add.", assumptions=["a + b"], question="", how_to_test="Open /?a=2&b=3")
