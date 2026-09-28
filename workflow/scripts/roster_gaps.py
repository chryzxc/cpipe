#!/usr/bin/env python3
"""Roles in team-config `bots:` that have no working bot in ~/.hermes/roster.yaml.

team-config.yaml is the single list of required roles; roster.yaml maps each to a profile.
Shared by delivery_submit (refuse to hand off), stall_alert (tell the operator), and
check_delivery_config (install-time check). Prints the gaps when run directly.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

TEAM_CONFIG = "skills/my-software-delivery-orchestrator/references/team-config.yaml"


def _home(home=None) -> Path:
    return Path(home or os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def required_roles(home=None) -> list[str]:
    """`bots:` keys in team-config, spelled the roster way (spike-explorer -> spike_explorer)."""
    text = (_home(home) / TEAM_CONFIG).read_text()
    return [r.replace("-", "_") for r in re.findall(r"^\s{2}([\w-]+):\s*\{mission:", text, re.M)]


def roster(home=None) -> dict[str, str]:
    """role -> profile from roster.yaml `roles:` (empty profile when the value is blank)."""
    path, found, in_roles = _home(home) / "roster.yaml", {}, False
    if not path.is_file():
        return found
    for line in path.read_text().splitlines():
        if line.strip() == "roles:":
            in_roles = True
        elif in_roles and line.startswith(" ") and ":" in line:
            role, profile = line.strip().split(":", 1)
            found[role.strip()] = profile.split("#", 1)[0].strip()
        elif line.strip() and not line.startswith((" ", "#")):
            in_roles = False
    return found


def roster_gaps(roles=None, home=None) -> dict[str, str]:
    """{role: problem} for each role (default: all required) that no existing profile serves."""
    home = _home(home)
    mapped = roster(home)
    gaps = {}
    for role in roles or required_roles(home):
        profile = mapped.get(role)
        if not profile:
            gaps[role] = "no bot assigned"
        elif not (home / "profiles" / profile).is_dir():
            gaps[role] = f"assigned bot '{profile}' does not exist"
    return gaps


def fix_hint(role: str) -> str:
    return f"add `  {role}: <profile>` under `roles:` in ~/.hermes/roster.yaml"


if __name__ == "__main__":
    gaps = roster_gaps()
    for role, problem in gaps.items():
        print(f"{role}: {problem} — {fix_hint(role)}")
    sys.exit(1 if gaps else 0)
