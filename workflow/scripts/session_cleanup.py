#!/usr/bin/env python3
"""Session hygiene: prune automation-created Hermes sessions so the
conversation list stays readable. Deterministic, no LLM.

Cron scheduler sessions (source `cron`, e.g. the Kanban stall supervisor's
15-minute ticks) and supervisor coordinator wake chats (source `cli`, title
`Kanban supervisor wake:`) are pure automation byproducts; kanban cards and
comments carry the canonical evidence, not these transcripts.

Retention (env-overridable):
  SESSION_CLEANUP_CRON_RETENTION_HOURS  default 48  (cron ticks)
  SESSION_CLEANUP_WAKE_RETENTION_HOURS  default 168 (7d wake chats)

Pinned and archived sessions are never pruned (engine CLI default). Runs
for real by default; pass --dry-run to preview. Exit 0 always.
"""
import os
import subprocess
import sys
from pathlib import Path

H = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))
HERMES = H / 'hermes-agent/venv/bin/hermes'
CRON_RETENTION_ENV = 'SESSION_CLEANUP_CRON_RETENTION_HOURS'
WAKE_RETENTION_ENV = 'SESSION_CLEANUP_WAKE_RETENTION_HOURS'
CRON_RETENTION_DEFAULT_H = 48
WAKE_RETENTION_DEFAULT_H = 168
WAKE_TITLE = 'Kanban supervisor wake:'


def retention_hours(env_key, default):
    raw = os.environ.get(env_key)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def prune_args(age_hours, filters, yes):
    """Engine CLI args for one prune pass. Pinned/archived stay untouched:
    the CLI only touches them with --include-pinned/--include-archived."""
    args = ['sessions', 'prune', '--older-than', f'{age_hours}h', *filters]
    args.append('--yes' if yes else '--dry-run')
    return args


def run_prune(hermes, age_hours, filters, yes):
    cmd = [str(hermes), *prune_args(age_hours, filters, yes)]
    label = ' '.join(filters) if filters else 'all sources'
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f'{label}: prune failed ({exc})'
    out = (result.stdout or result.stderr or '').strip()
    if result.returncode != 0:
        tail = out.splitlines()[-1] if out else f'rc={result.returncode}'
        return f'{label}: prune failed ({tail})'
    lines = [line for line in out.splitlines() if line.strip()]
    return f'{label}: {lines[-1] if lines else "no output"}'


def main():
    yes = '--dry-run' not in sys.argv
    report = []
    if not HERMES.exists():
        report.append(f'hermes binary not found at {HERMES}')
    else:
        cron_h = retention_hours(CRON_RETENTION_ENV, CRON_RETENTION_DEFAULT_H)
        wake_h = retention_hours(WAKE_RETENTION_ENV, WAKE_RETENTION_DEFAULT_H)
        report.append(f'cron sessions older than {cron_h}h — ' +
                      run_prune(HERMES, cron_h, ['--source', 'cron'], yes))
        report.append(f'wake sessions older than {wake_h}h — ' +
                      run_prune(HERMES, wake_h, ['--source', 'cli', '--title', WAKE_TITLE], yes))
    mode = 'prune' if yes else 'dry-run'
    (H / 'logs').mkdir(parents=True, exist_ok=True)
    (H / 'logs' / 'session-hygiene-digest.md').write_text(
        '# Session hygiene digest\n\n' + '\n'.join(f'- {r}' for r in report) + '\n')
    print('\n'.join(report) or 'nothing to do')


if __name__ == '__main__':
    main()
