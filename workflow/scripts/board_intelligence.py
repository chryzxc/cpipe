#!/usr/bin/env python3
"""Weekly board intelligence digest. Deterministic, no LLM."""
import os
import json
import re
import sqlite3
import time
from pathlib import Path

H = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))
DB = H / 'kanban.db'
WEEK = 7 * 86400

FINDING_CLASSES = (
    ('timeout-flake', ('timeout', 'timed out', 'flak', 'intermittent', 'hang')),
    ('missing-evidence', ('missing evidence', 'no evidence', 'evidence block', 'evidence missing', 'unverified')),
    ('contract-trace', ('contract trace', 'end-to-end contract', 'contract boundary')),
    ('test-after', ('test-after', 'red evidence', 'no red', 'tests added after', 'decorative test')),
    ('scope-creep', ('scope creep', 'out of scope', 'unrelated change', 'scope expansion')),
    ('unsafe-pattern', ('unsafe', 'force unwrap', 'injection', 'credential', 'secret', 'insecure')),
)


def _classify(text):
    for cls, keys in FINDING_CLASSES:
        if any(re.search(rf'\b{re.escape(k)}', text) for k in keys):
            return cls
    return None


def finding_recurrence(conn, now, window_days=28, threshold=3):
    """Group reviewer REQUEST_CHANGES comments into coarse classes; return (class, count, example) >= threshold."""
    since = now - window_days * 86400
    rows = conn.execute(
        "SELECT c.task_id, c.body FROM task_comments c JOIN tasks t ON t.id = c.task_id "
        "WHERE c.created_at > ? AND c.body LIKE '%REQUEST_CHANGES%'", (since,)).fetchall()
    counts, examples = {}, {}
    for tid, body in rows:
        cls = _classify((body or '').lower())
        if cls:
            counts[cls] = counts.get(cls, 0) + 1
            examples.setdefault(cls, tid)
    return [(cls, counts[cls], examples[cls])
            for cls in sorted(counts, key=counts.get, reverse=True) if counts[cls] >= threshold]


def q(sql, params=()):
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def main():
    now = time.time()
    lines = ['# Board intelligence', '']

    completed = q("SELECT id, assignee, created_at, started_at, completed_at FROM tasks "
                  "WHERE status='done' AND completed_at > ? ORDER BY completed_at", (now - WEEK,))
    lines.append(f'## Throughput (7d): {len(completed)} cards completed')
    slow = []
    for t in completed:
        total = ((t['completed_at'] or now) - t['created_at']) / 60
        wait = ((t['started_at'] or t['created_at']) - t['created_at']) / 60 if t['started_at'] else None
        work = ((t['completed_at'] or now) - (t['started_at'] or t['created_at'])) / 60
        slow.append((total, t['id'], t['assignee'], wait, work))
    slow.sort(reverse=True)
    if slow:
        avg_total = sum(s[0] for s in slow) / len(slow)
        avg_wait = sum(s[3] or 0 for s in slow) / len(slow)
        lines.append(f'- avg total {avg_total:.0f}m (queue {avg_wait:.0f}m + work '
                     f'{avg_total - avg_wait:.0f}m); slowest: ' +
                     '; '.join(f'{s[1]} ({s[0]:.0f}m, {s[2]})' for s in slow[:3]))

    runs = q("SELECT r.task_id, t.assignee, r.status, r.outcome FROM task_runs r "
             "LEFT JOIN tasks t ON t.id = r.task_id WHERE r.started_at > ?", (now - WEEK,))
    verdicts = {}
    for r in runs:
        key = (r['assignee'] or '?', r['status'])
        verdicts[key] = verdicts.get(key, 0) + 1
    lines.append('## Gate activity (7d, by run status)')
    for (assignee, status), n in sorted(verdicts.items(), key=lambda kv: -kv[1])[:8]:
        lines.append(f'- {assignee}/{status}: {n}')
    rejections = sum(n for (a, s), n in verdicts.items() if s in ('ready', 'blocked') and a == 'sentry')
    lines.append(f'- sentry change-request/verdict runs: {rejections}')

    rework = q("SELECT task_id, COUNT(*) c FROM task_runs WHERE started_at > ? "
               "GROUP BY task_id HAVING c >= 3 ORDER BY c DESC LIMIT 5", (now - WEEK,))
    if rework:
        lines.append('## Rework loops (>=3 runs)')
        lines.extend(f'- {r["task_id"]}: {r["c"]} runs' for r in rework)

    failures = q("SELECT outcome, COUNT(*) c FROM task_runs WHERE started_at > ? AND outcome IN "
                 "('crashed','timed_out','failed','gave_up') GROUP BY outcome", (now - WEEK,))
    if failures:
        lines.append('## Failures (7d)')
        lines.extend(f'- {f["outcome"]}: {f["c"]}' for f in failures)

    stale = q("SELECT id, status, assignee, created_at FROM tasks WHERE status IN ('todo','blocked') "
              "ORDER BY created_at LIMIT 3")
    if stale:
        lines.append('## Oldest non-running cards')
        lines.extend(f'- {s["id"]} ({s["status"]}, {s["assignee"]}, {(now - s["created_at"]) / 86400:.1f}d old)'
                     for s in stale)

    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    recurrent = finding_recurrence(conn, now)
    conn.close()
    if recurrent:
        lines.append('## Finding-class recurrence (28d)')
        lines.extend(f'- {cls}: {n} (e.g. {tid})' for cls, n, tid in recurrent)

    metrics_log = H / 'logs' / 'delivery-metrics.jsonl'
    if metrics_log.is_file():
        sessions = [json.loads(x) for x in metrics_log.read_text().splitlines()[-500:] if x.strip()]
        recent = [s for s in sessions if now - s.get('ts', 0) < WEEK]
        if recent:
            lines.append(f'## Sessions (plugin hook, 7d): {len(recent)} recorded')

    digest = '\n'.join(lines) + '\n'
    (H / 'logs').mkdir(parents=True, exist_ok=True)
    out = H / 'logs' / 'board-intelligence.md'
    out.write_text(digest)
    print(digest)


if __name__ == '__main__':
    main()
