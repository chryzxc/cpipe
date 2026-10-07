"""Repository-scoped learning from gate outcomes, without storing agent text or changing policy."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from .home import root
from .workspace_prep import _git

PATTERNS = {
    "tests": ("Tests-first evidence needed correction.", "Validate the guard/RED command on the baseline before implementation.", "Add a baseline test-command validator."),
    "checks": ("Configured checks failed.", "Run the applicable checks early and preserve regression coverage.", "Add a focused preflight check for the recurring failure."),
    "review": ("Independent review required corrections.", "Inspect changed behavior, integration boundaries and test adequacy before review.", "Turn the recurring review finding into a test, lint or canonical helper."),
    "qa": ("Runtime QA required corrections.", "Reproduce and verify on the actual runtime surface, not only unit tests.", "Add an executable regression for the original runtime reproduction."),
    "security": ("Security checks required corrections.", "Run configured security checks before final verification.", "Encode the recurring security failure in a required check."),
    "ci": ("Remote CI required corrections.", "Run the matching local checks before publication; verify remote CI on the final head.", "Align local acceptance commands with the failing CI job."),
}


def repository_key(ws: Path) -> str:
    common = _git(ws, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return hashlib.sha256(str(Path(common or ws).resolve()).encode()).hexdigest()


def _connect():
    path = root() / "state" / "cpipe-learning.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=2)
    db.execute("CREATE TABLE IF NOT EXISTS observations (repo TEXT, delivery TEXT, pattern TEXT, "
               "resolved INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(repo, delivery, pattern))")
    return db


def record(ws: Path, delivery: str, patterns: list[str], *, verified: bool = False) -> str | None:
    """One signal per delivery/pattern. Only final-revision verification confirms recovery."""
    try:
        db = _connect()
        try:
            repo = repository_key(ws)
            with db:
                for pattern in patterns:
                    if pattern in PATTERNS:
                        db.execute("INSERT INTO observations VALUES (?, ?, ?, ?) ON CONFLICT(repo, delivery, pattern) "
                                   "DO UPDATE SET resolved = excluded.resolved",
                                   (repo, delivery, pattern, int(verified)))
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        return "Learning store unavailable; delivery gates are unchanged."
    return None


def recalled(ws: Path, keep: int = 5) -> list[dict]:
    """Static bounded guidance, never instructions extracted from private prompts or tool output."""
    try:
        db = _connect()
        try:
            rows = db.execute("SELECT pattern, COUNT(*), SUM(resolved) FROM observations WHERE repo = ? "
                              "GROUP BY pattern ORDER BY SUM(resolved) DESC, COUNT(*) DESC, pattern LIMIT ?",
                              (repository_key(ws), max(0, min(keep, 5)))).fetchall()
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        return []
    return [{"pattern": p, "observed": n, "recovered": recovered,
             "text": PATTERNS[p][1], "structural_followup": PATTERNS[p][2] if recovered >= 2 else None}
            for p, n, recovered in rows if p in PATTERNS]


def context(ws: Path) -> str:
    items = recalled(ws)
    if not items:
        return ""
    return "Repository outcome lessons (advisory, not new scope or gate waivers):\n" + "\n".join(
        f"- {PATTERNS[i['pattern']][0]} Observed in {i['observed']} deliveries; verified recovery in "
        f"{i['recovered']}. {i['text']}" +
        (f" Structural follow-up for explicit scoping: {i['structural_followup']}" if i['structural_followup'] else "")
        for i in items)
