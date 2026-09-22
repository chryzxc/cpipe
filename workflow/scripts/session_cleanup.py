#!/usr/bin/env python3
"""Session hygiene: keep automation-created Hermes sessions out of the
conversation list. Deterministic, no LLM.

Cron scheduler sessions (source `cron` — the Kanban stall supervisor's
15-minute ticks, watchers, digests) are transient: their output is also
persisted in ~/.hermes/cron/output (kept 30 days by housekeeping), so the
transcripts can be hidden after an hour. and supervisor coordinator wake chats
(source `cli`, title `Kanban supervisor wake:`) are pure automation
byproducts; kanban cards and comments carry the canonical evidence, not
these transcripts.

Two tiers:
  default      archive (soft-hide) — safe while the gateway is live, the
               list cleans up immediately, nothing is deleted
  --prune      additionally attempt real deletion of sessions older than
               PRUNE_RETENTION (default 30d); the engine refuses while the
               gateway holds state.db, which is reported, never forced

Retention (env-overridable hours):
  SESSION_CLEANUP_CRON_RETENTION_HOURS   default 1   (archive cutoff, cron)
  SESSION_CLEANUP_WAKE_RETENTION_HOURS   default 168 (archive cutoff, wakes)
  SESSION_CLEANUP_PRUNE_RETENTION_HOURS  default 720 (30d deletion cutoff)

Pinned sessions are never touched (engine CLI default). Pass --dry-run to
preview. Exit 0 always.
"""
import os
import subprocess
import sys
from pathlib import Path

H = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))
HERMES = H / 'hermes-agent/venv/bin/hermes'
CRON_RETENTION_ENV = 'SESSION_CLEANUP_CRON_RETENTION_HOURS'
WAKE_RETENTION_ENV = 'SESSION_CLEANUP_WAKE_RETENTION_HOURS'
PRUNE_RETENTION_ENV = 'SESSION_CLEANUP_PRUNE_RETENTION_HOURS'
CRON_RETENTION_DEFAULT_H = 1
WAKE_RETENTION_DEFAULT_H = 168
PRUNE_RETENTION_DEFAULT_H = 720
WAKE_TITLE = 'Kanban supervisor wake:'
LIVE_DB_MARKERS = ('another process is using', 'Refusing')


def retention_hours(env_key, default):
    raw = os.environ.get(env_key)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def archive_args(age_hours, filters, yes=True):
    """Engine CLI args for one soft-hide pass. Pinned sessions stay pinned:
    the CLI only includes them with --include-pinned."""
    return ['sessions', 'archive', '--older-than', f'{age_hours}h', *filters,
            '--yes' if yes else '--dry-run']


def prune_args(age_hours, filters, yes=True):
    """Engine CLI args for one deletion pass. --include-archived lets the
    prune also reclaim already-archived automation sessions; pinned stays
    excluded. The engine refuses under a live gateway — never force it."""
    return ['sessions', 'prune', '--older-than', f'{age_hours}h',
            '--include-archived', *filters, '--yes' if yes else '--dry-run']


def run_engine(hermes, args):
    try:
        result = subprocess.run([str(hermes), *args],
                                capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f'failed ({exc})', []
    out = (result.stdout or '').strip()
    err = (result.stderr or '').strip()
    if result.returncode != 0:
        combined = out + '\n' + err
        if any(marker in combined for marker in LIVE_DB_MARKERS):
            return 'skipped — gateway holds state.db (stop it to reclaim disk)', []
        tail = err.splitlines()[-1] if err else (out.splitlines()[-1] if out else f'rc={result.returncode}')
        return f'failed ({tail})', []
    lines = [line for line in out.splitlines() if line.strip()]
    return (lines[-1] if lines else 'no output'), lines


def matched_count(lines):
    for line in lines:
        for key in ('session(s) match', 'Archived', 'Deleted'):
            if key in line:
                return line.strip()
    return ''


def main():
    argv = set(sys.argv[1:])
    dry_run = '--dry-run' in argv
    do_prune = '--prune' in argv
    yes = not dry_run
    suffix = ' --dry-run' if dry_run else ''
    report = []

    if not HERMES.exists():
        report.append(f'hermes binary not found at {HERMES}')
    else:
        cron_h = retention_hours(CRON_RETENTION_ENV, CRON_RETENTION_DEFAULT_H)
        wake_h = retention_hours(WAKE_RETENTION_ENV, WAKE_RETENTION_DEFAULT_H)
        passes = [
            (f'archive cron sessions older than {cron_h}h{suffix}',
             archive_args(cron_h, ['--source', 'cron'], yes)),
            (f'archive wake chats older than {wake_h}h{suffix}',
             archive_args(wake_h, ['--source', 'cli', '--title', WAKE_TITLE], yes)),
        ]
        if do_prune:
            prune_h = retention_hours(PRUNE_RETENTION_ENV, PRUNE_RETENTION_DEFAULT_H)
            passes.append(
                (f'prune automation sessions older than {prune_h}h{suffix}',
                 prune_args(prune_h, ['--source', 'cron'], yes)))

        for label, args in passes:
            outcome, lines = run_engine(HERMES, args)
            detail = matched_count(lines)
            report.append(f'{label}: {outcome}' + (f' — {detail}' if detail else ''))

    (H / 'logs').mkdir(parents=True, exist_ok=True)
    (H / 'logs' / 'session-hygiene-digest.md').write_text(
        '# Session hygiene digest\n\n' + '\n'.join(f'- {r}' for r in report) + '\n')
    print('\n'.join(report) or 'nothing to do')


if __name__ == '__main__':
    main()
