"""Import-only startup receipt; deliberately imports no dependencies at top level."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import sys

MODULES = ("yaml", "ruamel.yaml", "hermes_cli.kanban_db", "hermes_cli._launchers",
           "cpipe.settings", "cpipe.round", "cpipe.runtime.scheduler", "cpipe.runtime.supervisor")


def check() -> dict:
    failures = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            # Never include environment values or dependency exception text.
            failures.append({"module": name, "category": "dependency" if isinstance(exc, ImportError)
                             else "capability", "error_type": type(exc).__name__})
    return {"ready": not failures, "python": sys.executable,
            "package": str(Path(__file__).resolve().parents[2]),
            "home": os.environ.get("HERMES_HOME"), "failures": failures}


def main() -> int:
    receipt = check()
    print(json.dumps(receipt), flush=True)
    return 0 if receipt["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
