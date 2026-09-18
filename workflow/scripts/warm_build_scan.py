#!/usr/bin/env python3
"""Background warm-build of todo-card worktrees. Deterministic, no LLM."""
import os, sqlite3, subprocess, time
from pathlib import Path

H = Path.home() / '.hermes'
DB = H / 'kanban.db'
conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
rows = conn.execute("SELECT id, workspace_path FROM tasks WHERE status='todo' AND workspace_path IS NOT NULL").fetchall()
conn.close()
for tid, ws in rows:
    ws = Path(ws)
    if not ws.is_dir() or ws.parent.name.startswith('.'):
        continue
    if not (ws / 'Package.swift').is_file():
        continue
    marker, building = ws / '.warm-built', ws / '.warm-building'
    if marker.exists() or building.exists():
        continue
    building.touch()
    log = open(H / 'kanban/logs' / f'{tid}-warmbuild.log', 'w')
    script = f'cd {ws} && swift build --disable-automatic-resolution; rc=$?; rm -f {building}; [ $rc -eq 0 ] && touch {marker}; exit 0'
    subprocess.Popen(['/bin/bash', '-c', script], stdout=log, stderr=log, start_new_session=True)
    time.sleep(1)
