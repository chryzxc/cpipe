#!/usr/bin/env python3
"""Deterministic delivery-config validator. Exit 1 on any failure."""
import os
import re, sqlite3, subprocess, sys
from pathlib import Path

H = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))
CFG = (H / 'config.yaml').read_text()
SKILL = H / 'skills/my-software-delivery-orchestrator'
TEAM = (SKILL / 'references/team-config.yaml').read_text()
errors = []

def cap(key):
    m = re.search(rf'{key}:\s*(\d+)', CFG)
    return int(m.group(1)) if m else None

board, per_profile = cap('max_in_progress'), cap('max_in_progress_per_profile')
if not board or not per_profile:
    errors.append(f'config: kanban caps missing (board={board}, per_profile={per_profile})')
swarm = re.search(r'max (\d+) concurrent forge workers', TEAM)
if swarm and per_profile and int(swarm.group(1)) != per_profile:
    errors.append(f'drift: team-config swarm cap {swarm.group(1)} != engine per-profile cap {per_profile}')

def _flat_section(path, section):
    found, current = {}, None
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not line[:1].isspace() and line.rstrip().endswith(':'):
            current = line.strip()[:-1]
            continue
        m = re.match(r'\s*([\w-]+):\s*(\S+)', line)
        if m and current == section:
            found[m.group(1)] = m.group(2)
    return found


roster = {}
roster_path = H / 'roster.yaml'
if roster_path.is_file():
    roster = _flat_section(roster_path, 'roles')
team_roles = set(re.findall(r'^\s{2}(\w+):\s*\{mission:', TEAM, re.M))
for role in team_roles:
    if roster and role not in roster:
        errors.append(f'roster: role {role} missing from ~/.hermes/roster.yaml')
for role, profile in roster.items():
    if profile and not (H / 'profiles' / str(profile)).is_dir():
        errors.append(f'roster: role {role} -> profile {profile} not found in ~/.hermes/profiles')

if (H / 'kanban.db').is_file():
    conn = sqlite3.connect(f"file:{H/'kanban.db'}?mode=ro", uri=True)
    rows = conn.execute("SELECT id, skills, body, max_runtime_seconds FROM tasks WHERE status IN ('todo','ready')").fetchall()
    for tid, skills, body, maxrt in rows:
        if body and 'token_budget' not in body:
            errors.append(f'card {tid}: missing token_budget')
        if maxrt and maxrt > 1800:
            errors.append(f'card {tid}: max_runtime {maxrt}s exceeds 30min ceiling')
        for s in (skills or '[]').replace('[','').replace(']','').replace('"','').split(','):
            s = s.strip()
            if not s:
                continue
            if not (H / 'skills' / s).is_dir() and not any((c / s).is_dir() for c in (H / 'skills').iterdir() if c.is_dir()):
                errors.append(f'card {tid}: skill {s} not in global catalog')
    conn.close()

print('CONFIG CHECK OK' if not errors else 'CONFIG CHECK FAILURES:\n' + '\n'.join(errors))
sys.exit(1 if errors else 0)
