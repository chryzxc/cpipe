"""How each delivery went, from the board: the record the learning loop reads.

    python3 -m cpipe.outcomes [--days 28] [--json]

A delivery is its first card plus every card naming it (`DELIVERY: <id>`). Nothing is stored: the board
is the record, so a later fix_of on the same delivery shows up on the next run. Deterministic, read-only,
and dependency-free so the weekly digest script can load it by path.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from pathlib import Path

DONE = ("done", "archived")
VERDICT_FAIL = re.compile(r"VERDICT:\s*FAIL\b")
NOISE = re.compile(r"\b[0-9a-f]{7,40}\b|\d+(\.\d+)?|/[\w./-]+/")  # shas, numbers, paths


def _db() -> Path:
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return Path(os.environ.get("HERMES_KANBAN_DB") or home / "kanban.db")


def failure_lines(result: str | None) -> list[str]:
    """The failures a FAIL verdict lists: the non-empty lines after the VERDICT line, at most 3."""
    text = result or ""
    m = VERDICT_FAIL.search(text)
    if not m:
        return []
    lines = [l.strip(" -*\t") for l in text[m.end():].splitlines()[1:]]
    return [l[:160] for l in lines if l][:3]


def deliveries(conn, since: float = 0) -> list[dict]:
    """One dict per delivery whose first card was created after `since`."""
    conn.row_factory = sqlite3.Row
    roots = conn.execute(
        "SELECT * FROM tasks WHERE workflow_template_id IS NOT NULL AND workflow_template_id != 'design' "
        "AND instr(coalesce(body, ''), 'DELIVERY: t_') = 0 AND created_at > ? ORDER BY created_at",
        (since,)).fetchall()
    out = []
    for root in roots:
        cards = [root, *conn.execute("SELECT * FROM tasks WHERE instr(body, ?) > 0 ORDER BY created_at",
                                     (f"DELIVERY: {root['id']}",)).fetchall()]
        ids = [c["id"] for c in cards]
        marks = ",".join("?" * len(ids))
        parents = dict(conn.execute(
            f"SELECT l.child_id, p.current_step_key FROM task_links l JOIN tasks p ON p.id = l.parent_id "
            f"WHERE l.child_id IN ({marks})", ids).fetchall())
        changes = conn.execute(f"SELECT count(*) FROM task_events WHERE kind = 'changes_requested' "
                               f"AND task_id IN ({marks})", ids).fetchone()[0]
        fixes = [c for c in cards if c["current_step_key"] == "fix"]
        fails = [c for c in cards if c["current_step_key"] == "verify" and VERDICT_FAIL.search(c["result"] or "")]
        ended = [c["completed_at"] for c in cards if c["completed_at"]]
        out.append({
            "id": root["id"], "project": root["project_id"] or "", "flow": root["workflow_template_id"],
            "title": root["title"], "cards": len(cards),
            "open": any(c["status"] not in DONE for c in cards),
            "review_rounds": changes,
            "fix_rounds": sum(parents.get(c["id"]) == "verify" for c in fixes),
            "user_fixes": sum(parents.get(c["id"]) != "verify" for c in fixes),  # you found what the team missed
            "verify_fails": len(fails),
            "failures": [l for c in fails for l in failure_lines(c["result"])],
            "minutes": round((max(ended) - root["created_at"]) / 60) if ended else None,
        })
    return out


def recurring(rows: list[dict], threshold: int = 2) -> list[tuple[str, str, int, str]]:
    """Failures seen in `threshold`+ deliveries of one project: (project, failure, count, example).
    These are the lesson candidates."""
    seen: dict[tuple[str, str], set] = {}
    example: dict[tuple[str, str], str] = {}
    for row in rows:
        for line in row["failures"]:
            key = (row["project"], NOISE.sub("#", line.lower())[:80])
            seen.setdefault(key, set()).add(row["id"])
            example.setdefault(key, line)
    return sorted(((p, example[(p, k)], len(ids), sorted(ids)[0]) for (p, k), ids in seen.items()
                   if len(ids) >= threshold), key=lambda r: -r[2])


def report(rows: list[dict]) -> str:
    """Per project: deliveries, average rounds, the ones you had to fix, and lesson candidates."""
    lines = []
    for project in sorted({r["project"] for r in rows}):
        mine = [r for r in rows if r["project"] == project]
        n = len(mine)
        lines.append(f"## {project or '(no project)'}: {n} deliveries ({sum(r['open'] for r in mine)} open)")
        lines.append(f"- per delivery: review rounds {sum(r['review_rounds'] for r in mine) / n:.1f}, "
                     f"fix rounds {sum(r['fix_rounds'] for r in mine) / n:.1f}, "
                     f"verify fails {sum(r['verify_fails'] for r in mine) / n:.1f}")
        missed = [r for r in mine if r["user_fixes"]]
        if missed:
            lines.append("- you sent fixes for: " + "; ".join(f"{r['id']} {r['title'][:50]}" for r in missed))
        for _, failure, count, ex in recurring(mine):
            lines.append(f"- LESSON CANDIDATE ({count} deliveries, e.g. {ex}): {failure}")
    return "\n".join(lines) or "No tagged deliveries in this window."


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=float, default=28)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    conn = sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)
    rows = deliveries(conn, time.time() - args.days * 86400)
    print(json.dumps(rows, indent=1) if args.json else report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
