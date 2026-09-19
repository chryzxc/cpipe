#!/usr/bin/env python3
"""Print the Hermes profile mapped to one workflow role."""
import os
import sys
from pathlib import Path


def load_roles(path: Path) -> dict[str, str]:
    roles: dict[str, str] = {}
    in_roles = False
    for line in path.read_text().splitlines():
        if line.strip() == "roles:":
            in_roles = True
            continue
        if in_roles and line.startswith(" ") and ":" in line:
            role, profile = line.strip().split(":", 1)
            roles[role] = profile.split("#", 1)[0].strip()
    return roles


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: resolve_role.py ROLE", file=sys.stderr)
        return 2
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    roster = home / "roster.yaml"
    if not roster.is_file():
        print(f"missing roster: {roster}", file=sys.stderr)
        return 1
    profile = load_roles(roster).get(sys.argv[1])
    if not profile:
        print(f"unmapped role: {sys.argv[1]}", file=sys.stderr)
        return 1
    print(profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
