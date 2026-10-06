#!/usr/bin/env python3
"""A scripted stand-in for ``codex exec [resume <thread>] --json -o F ... <prompt>`` on the python fixture.

    first turn            -> a wrong fix (add multiplies), committed: the tests check fails
    "checks failed"       -> the right fix, committed
    "New input"           -> a commit that addresses the feedback
    FAKE_CODEX=rate_limit -> an error event and no result

Every call is appended to $FAKE_CODEX_LOG as {"thread", "resumed", "prompt"}.
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
with open(os.environ["FAKE_CODEX_LOG"], "a") as f:
    f.write(json.dumps({"thread": thread, "resumed": resumed, "prompt": prompt}) + "\n")


def emit(**ev):
    print(json.dumps(ev), flush=True)


def commit(code: str, msg: str):
    (cwd / "calc.py").write_text(code)
    git = ["git", "-c", "user.email=fake@codex", "-c", "user.name=fake"]
    subprocess.run([*git, "commit", "-qam", msg], cwd=cwd, check=True)


if not resumed:
    emit(type="thread.started", thread_id=thread)
emit(type="turn.started")
if os.environ.get("FAKE_CODEX") == "rate_limit":
    emit(type="error", message="You've hit your usage limit. Try again later.")
    sys.exit(1)
if "New input" in prompt:
    commit((cwd / "calc.py").read_text() + "\n\n# feedback addressed\n", "chore: address feedback")
elif "checks failed" in prompt:
    commit("def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n", "fix: add adds")
else:
    commit("def add(a, b):\n    return a * b\n\n\ndef mul(a, b):\n    return a * b\n", "fix: add")
emit(type="item.completed", item={"type": "agent_message", "text": "done"})
emit(type="turn.completed", usage={"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 50})
out_file.write_text(json.dumps({"status": "done", "summary": "Fixed add.", "assumptions": ["a + b"], "question": ""}))
