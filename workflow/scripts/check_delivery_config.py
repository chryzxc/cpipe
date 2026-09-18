#!/usr/bin/env python3
"""Deterministic delivery-config validator. Exit 1 on any failure."""
import re, sqlite3, subprocess, sys
from pathlib import Path

H = Path.home() / '.hermes'
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

roster = {p.name for p in (H / 'profiles').iterdir() if p.is_dir()}
for bot in re.findall(r'^\s{2}(\w+):\s*\{mission:', TEAM, re.M):
    if bot not in roster and bot != 'nexus' or bot == 'nexus' and 'default' not in roster:
        errors.append(f'roster: team-config bot {bot} missing from ~/.hermes/profiles')

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
