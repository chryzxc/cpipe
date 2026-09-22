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
  default      archive (soft-hide) ended automation sessions — safe while
               the gateway is live; the list cleans up immediately
  delete pass  cron ticks that stay OPEN are invisible to archive/prune, so
               listed cron sessions older than 30 minutes are deleted
               explicitly (cron_<id> rows only; bounded per run)
  --prune      additionally attempt real deletion of archived sessions
               older than PRUNE_RETENTION (default 30d); the engine
               refuses while the gateway holds state.db, which is
               reported, never forced

Retention (env-overridable hours):
  SESSION_CLEANUP_CRON_RETENTION_HOURS   default 1   (archive cutoff, cron)
  SESSION_CLEANUP_WAKE_RETENTION_HOURS   default 168 (archive cutoff, wakes)
  SESSION_CLEANUP_PRUNE_RETENTION_HOURS  default 720 (30d deletion cutoff)

Pinned sessions are never touched (engine CLI default). Pass --dry-run to
preview. Exit 0 always.
"""
import json
import os
import re
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
DELETE_MIN_AGE_MINUTES = 30
DELETE_PER_RUN_CAP = 100
CRON_ID_RE = re.compile(r'^cron_[0-9a-f]+_\d{8}_\d{6}$')
AGE_TOKEN_RE = re.compile(r'^(?:(\d+)m|(\d+)h|(\d+)d|yesterday|\d{2}-\d{2})$')
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


def parse_age_minutes(row):
    """Minutes since last activity from one `sessions list` row, or None
    when the age column cannot be parsed (never delete on doubt)."""
    tokens = row.split()
    if len(tokens) < 2:
        return None
    for token in reversed(tokens[:-1]):
        match = AGE_TOKEN_RE.match(token)
        if not match:
            continue
        minutes, hours, days = match.groups()
        if minutes:
            return int(minutes)
        if hours:
            return int(hours) * 60
        if days:
            return int(days) * 1440
        return 1440  # yesterday / date form
    return None


def open_cron_ids(listing):
    """Session IDs still listed under source cron (open rows archive and
    prune both skip). Only strict cron_<id> rows qualify."""
    ids = []
    for row in listing.splitlines() if isinstance(listing, str) else listing:
        tokens = row.split()
        if not tokens or not CRON_ID_RE.match(tokens[-1]):
            continue
        if tokens[0].startswith(('─', 'Title', '…')):
            continue
        ids.append(tokens[-1])
    return ids


def pinned_ids(hermes):
    outcome, lines = run_engine(hermes, ['sessions', 'pinned', '--json'])
    if 'failed' in outcome or 'skipped' in outcome:
        return None
    try:
        return {entry['id'] for entry in json.loads('\n'.join(lines)) if entry.get('id')}
    except (json.JSONDecodeError, TypeError):
        return None


def delete_pass(hermes, listing, pinned, yes, dry_run):
    stale = []
    rows = listing.splitlines() if isinstance(listing, str) else listing
    for session_id in open_cron_ids(listing):
        if session_id in pinned:
            continue
        row = next((r for r in rows if r.rstrip().endswith(session_id)), '')
        age = parse_age_minutes(row)
        if age is None or age < DELETE_MIN_AGE_MINUTES:
            continue
        stale.append(session_id)
        if len(stale) >= DELETE_PER_RUN_CAP:
            break
    if dry_run:
        return f'{len(stale)} open cron session(s) would be deleted'
    deleted = 0
    for session_id in stale:
        result = run_engine(hermes, ['sessions', 'delete', session_id, '--yes'])
        if 'Deleted' in result[0] or result[0] == 'no output':
            deleted += 1
    return f'deleted {deleted} open cron session(s)'


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
        for label, args in passes:
            outcome, lines = run_engine(HERMES, args)
            detail = matched_count(lines)
            report.append(f'{label}: {outcome}' + (f' — {detail}' if detail else ''))

        outcome, listing = run_engine(HERMES, ['sessions', 'list', '--source', 'cron', '--limit', '500'])
        if 'failed' in outcome or 'skipped' in outcome:
            report.append(f'open cron sessions: {outcome}')
        else:
            pinned = pinned_ids(HERMES)
            if pinned is None:
                report.append('open cron sessions: skipped — pinned-session list unavailable')
            else:
                report.append(f'open cron sessions: {delete_pass(HERMES, listing, pinned, yes, dry_run)}')

        if do_prune:
            prune_h = retention_hours(PRUNE_RETENTION_ENV, PRUNE_RETENTION_DEFAULT_H)
            outcome, lines = run_engine(HERMES, prune_args(prune_h, ['--source', 'cron'], yes))
            detail = matched_count(lines)
            report.append(f'prune automation sessions older than {prune_h}h{suffix}: {outcome}' +
                          (f' — {detail}' if detail else ''))

    (H / 'logs').mkdir(parents=True, exist_ok=True)
    (H / 'logs' / 'session-hygiene-digest.md').write_text(
        '# Session hygiene digest\n\n' + '\n'.join(f'- {r}' for r in report) + '\n')
    print('\n'.join(report) or 'nothing to do')


if __name__ == '__main__':
    main()
