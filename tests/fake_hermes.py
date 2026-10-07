#!/usr/bin/env python3
"""Scripted Hermes stream-json CLI; reuses the calculator scenario, never calls a model or coding CLI."""
import io
import json
import os
import runpy
import sys
import tempfile
import uuid
from contextlib import redirect_stdout
from pathlib import Path

args = sys.argv[1:]
profile = args[args.index("-p") + 1]
cwd = Path(args[args.index("--in") + 1])
prompt = args[args.index("-q") + 1]
session = args[args.index("--resume") + 1] if "--resume" in args else f"{profile}-{uuid.uuid4().hex[:8]}"
with open(os.environ["FAKE_HERMES_LOG"], "a") as f:
    f.write(json.dumps({"profile": profile, "session": session, "resumed": "--resume" in args, "prompt": prompt}) + "\n")

# Adapt the existing deterministic scenario's result to Hermes's real wire contract.
# This is test data, not a replacement for performance evidence from actual models.
with tempfile.TemporaryDirectory(dir=cwd) as td:
    out = Path(td) / "result.json"
    sys.argv = [__file__, "exec", "resume", session, "--json", "-o", str(out), "-C", str(cwd), prompt]
    try:
        with redirect_stdout(io.StringIO()):
            runpy.run_path(str(Path(__file__).with_name("fake_codex.py")), run_name="__main__")
    except SystemExit as e:
        if e.code:
            raise
    data = json.loads(out.read_text())
print(json.dumps({"type": "result", "exit_code": 0, "session_id": session,
                  "tokens": {"input": 1000, "cache_read": 800, "output": 50},
                  "text": "```json\n" + json.dumps(data) + "\n```"}), flush=True)
