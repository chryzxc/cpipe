"""The Hermes root. Workers run with HERMES_HOME=<root>/profiles/<name>, but the roster, board and
delivery state the plugin reads all live at the root."""

import os
from pathlib import Path


def root() -> Path:
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    return home.parent.parent if home.parent.name == "profiles" else home
