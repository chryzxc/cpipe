#!/usr/bin/env python3
"""Weekly housekeeping: process cleanup, worktree GC, card archival,
log/cache retention, WAL checkpoint, skill drift report. Deterministic."""
import hashlib, sqlite3, subprocess, time
from pathlib import Path

H = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))
CUTOFF = time.time() - 30 * 86400
HERMES = H / 'hermes-agent/venv/bin/hermes'
report = []

out = subprocess.run(['ps', '-eo', 'pid,etime,command'], capture_output=True, text=True).stdout
for line in out.splitlines():
    if 'kanban tail' in line:
        parts = line.split()
        pid, etime = parts[0], parts[1]
        if '-' in etime or (':' in etime and int(etime.split(':')[0]) >= 1):
            subprocess.run(['kill', pid])
            report.append(f'killed orphaned kanban tail pid {pid} (age {etime})')

conn = sqlite3.connect(H / 'kanban.db')
now = time.time()

for tid, ws, status, completed in conn.execute(
        "SELECT id, workspace_path, status, completed_at FROM tasks "
        "WHERE workspace_path IS NOT NULL AND status IN ('done','archived')"):
    parents = [Path(x).expanduser() for x in
               os.environ.get('DELIVERY_WORKTREE_PARENTS', str(Path.home() / 'Projects')).split(':')]
    if completed and now - completed > 86400 and ws and '-issue' in Path(ws).name:
        p = Path(ws)
        if p.is_dir() and p.parent in parents:
            subprocess.run(['rm', '-rf', str(p)])
            report.append(f'worktree GC: removed {p.name} for {tid}')

for (tid,) in conn.execute("SELECT id FROM tasks WHERE status='done' AND completed_at < ?", (now - 14*86400,)):
    subprocess.run([str(HERMES), 'kanban', 'archive', tid], capture_output=True)
    report.append(f'archived done card {tid}')
conn.close()

targets = [H/'kanban/logs', H/'cron/output', H/'image_cache', H/'audio_cache',
           H/'bootstrap-cache', H/'state-snapshots', H/'workflow-backups', H/'skill-archives']
freed = 0
for d in targets:
    if not d.is_dir():
        continue
    for f in d.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < CUTOFF:
                freed += f.stat().st_size
                f.unlink()
        except OSError:
            pass
for f in H.glob('config.yaml.bak*'):
    try:
        if f.stat().st_mtime < CUTOFF:
            f.unlink()
    except OSError:
        pass
report.append(f'retention: freed {freed//1048576} MB (30-day cutoff)')

for db in (H/'kanban.db', H/'state.db'):
    try:
        c = sqlite3.connect(db)
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        c.close()
        report.append(f'checkpointed {db.name}')
    except sqlite3.Error as e:
        report.append(f'checkpoint FAILED {db.name}: {e}')

def digest(p):
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.is_file() else None

global_skills = H / 'skills'
drift = []
for prof in sorted((H/'profiles').iterdir()):
    if not prof.is_dir():
        continue
    pskills = prof / 'skills'
    if not pskills.is_dir():
        continue
    for cat in pskills.iterdir():
        if not cat.is_dir():
            continue
        for sk in cat.iterdir():
            if sk.is_dir():
                g = next((c/sk.name for c in global_skills.iterdir() if c.is_dir() and (c/sk.name).is_dir()), None)
                if g and digest(g/'SKILL.md') != digest(sk/'SKILL.md'):
                    drift.append(f'{prof.name}/{sk.name} differs from global')
report.append(f'skill drift: {len(drift)} copies differ' + ('' if not drift else ' — ' + '; '.join(drift[:10])))

outpath = H / 'logs' / 'housekeeping-digest.md'
outpath.write_text('# Housekeeping digest\n\n' + '\n'.join(f'- {r}' for r in report) + '\n')
print('\n'.join(report) or 'nothing to do')
