#!/usr/bin/env python3
"""Background warm-build of todo-card worktrees. Deterministic, no LLM."""
import os, sqlite3, subprocess, sys, time
from pathlib import Path

H = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))
DB = H / 'kanban.db'
STALE_BUILD_SECONDS = 30 * 60
BUILD_WRAPPER = '''
import pathlib
import subprocess
import sys

workspace, marker, building = map(pathlib.Path, sys.argv[1:])
try:
    result = subprocess.run(
        ['swift', 'build', '--disable-automatic-resolution'], cwd=workspace)
    if result.returncode == 0:
        marker.touch()
finally:
    building.unlink(missing_ok=True)
'''

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
    if marker.exists():
        continue
    if building.exists():
        if time.time() - building.stat().st_mtime < STALE_BUILD_SECONDS:
            continue
        building.unlink()
    building.touch()
    try:
        with open(H / 'kanban/logs' / f'{tid}-warmbuild.log', 'w') as log:
            subprocess.Popen(
                [sys.executable, '-c', BUILD_WRAPPER, str(ws), str(marker), str(building)],
                stdout=log, stderr=log, start_new_session=True)
    except OSError:
        building.unlink(missing_ok=True)
