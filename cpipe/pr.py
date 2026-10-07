"""The pr, ci and final nodes: cpipe's own code pushes the branch, opens or updates the pull request, watches CI
and marks the pull request ready. The engine never pushes.

Before anything leaves the machine, the public-text scan looks for local paths and board ids: in commit messages
and added lines they go back to the engine (``fix``); in the PR title and body (written from the engine's
summary) they are redacted.

Without ``gh`` or a GitHub remote the branch is still pushed and the delivery hands off without a pull request.
Without any remote (a new local project) the branch stays local.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

CODE_LEAK = re.compile(r"(?:/Users/|/home/)[\w.-]+/\S*|~/\.hermes\S*|\S*\.hermes/\S*|\S*\.worktrees/\S*"
                       r"|\bt_[0-9a-f]{8}\b")
# Prose also must not quote full commit hashes (they read as internal ids); links to /commit/<sha> are fine.
LEAK = re.compile(CODE_LEAK.pattern + r"|(?<!/commit/)(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")
POLL = 20
NO_CHECKS_WAIT = 120  # CI that never reports (path filters): stop waiting after this


def _git(ws: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True)


def _gh(ws: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], cwd=ws, capture_output=True, text=True, timeout=timeout)


def github(ws: Path) -> bool:
    return bool(shutil.which("gh")) and _gh(ws, "repo", "view", "--json", "url").returncode == 0


def base_ref(ws: Path, base: str) -> str:
    """``origin/<base>``, or the local ``<base>`` in a repo without that remote branch."""
    return f"origin/{base}" if _git(ws, "rev-parse", "--verify", "-q", f"origin/{base}").returncode == 0 else base


def redact(text: str, ws: Path) -> str:
    return LEAK.sub("[redacted]", (text or "").replace(str(ws), "."))


def leaks(ws: Path, base: str) -> list[str]:
    """Local paths and board ids in the branch's commit messages and added lines."""
    msgs = _git(ws, "log", "--format=%B", f"{base}..HEAD").stdout
    diff = _git(ws, "diff", f"{base}...HEAD").stdout
    added = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    return sorted({m.group(0) for text in (msgs, *added) for m in CODE_LEAK.finditer(text)})


def body(state: dict, ws: Path) -> str:
    out = ["## Summary", state.get("summary") or state["request"]]
    if state.get("assumptions"):
        out += ["", "## Assumptions", *(f"- {a}" for a in dict.fromkeys(state["assumptions"]))]
    if state.get("how_to_test"):
        out += ["", "## How to test", state["how_to_test"]]
    if state.get("verification"):
        out += ["", "## Verification", *(f"- {v}" for v in state["verification"])]
    return redact("\n".join(out), ws)


def publish(ws: Path, state: dict, branch: str) -> tuple[str, str]:
    """``(outcome, detail)``: ok, fix (leaks or no commits: back to the engine) or blocked (push failed)."""
    base = base_ref(ws, state["base"])
    if not _git(ws, "rev-list", f"{base}..HEAD").stdout.strip():
        return "fix", "There are no commits on the branch. Commit the change."
    found = leaks(ws, base)
    if found:
        return "fix", ("The branch's commit messages or added lines contain local paths or internal ids, which must "
                       "not be published: " + ", ".join(found[:10]) + ". Remove them (reword the commits too) and commit.")
    if _git(ws, "remote", "get-url", "origin").returncode != 0:
        state.setdefault("notes", {})["pr"] = f"No remote: the change is on the local branch {branch}."
        return "ok", state["notes"]["pr"]
    push = _git(ws, "push", "--force-with-lease", "origin", f"HEAD:refs/heads/{branch}")
    if push.returncode != 0:
        return "blocked", f"git push failed: {(push.stderr or push.stdout).strip()[-1500:]}"
    if not github(ws):
        state.setdefault("notes", {})["pr"] = "Pushed; no pull request (gh or a GitHub remote is not available)."
        return "ok", state["notes"]["pr"]
    title = redact(state["request"].splitlines()[0][:72], ws)
    text = body(state, ws)
    if state.get("pr_url"):
        done = _gh(ws, "pr", "edit", state["pr_url"], "--title", title, "--body", text)
    else:
        done = _gh(ws, "pr", "create", "--draft", "--base", state["base"], "--head", branch, "--title", title,
                   "--body", text)
        if done.returncode == 0:
            state["pr_url"] = done.stdout.strip().splitlines()[-1]
    if done.returncode != 0:
        return "blocked", f"gh failed: {(done.stderr or done.stdout).strip()[-1500:]}"
    return "ok", state["pr_url"]


def ci(ws: Path, state: dict, minutes: int) -> tuple[str, str]:
    """``(ok | fail | blocked, detail)``: waits for checks; pending/unavailable is never a pass."""
    if not state.get("pr_url") or not (ws / ".github" / "workflows").is_dir():
        return "ok", "no CI"
    expected = _git(ws, "rev-parse", "HEAD").stdout.strip()
    snapshot = _gh(ws, "pr", "view", state["pr_url"], "--json", "headRefOid")
    try:
        remote_head = json.loads(snapshot.stdout)["headRefOid"]
    except (ValueError, KeyError, TypeError):
        return "blocked", "Could not verify the pull request revision; no CI approval recorded"
    if snapshot.returncode or remote_head != expected:
        return "blocked", "The pull request head differs from the verified local revision; no CI approval recorded"
    start = time.time()
    while True:
        done = _gh(ws, "pr", "checks", state["pr_url"], "--json", "name,state,bucket,link")
        try:
            checks = json.loads(done.stdout or "[]")
        except ValueError:
            return "blocked", "CI check results could not be read; no approval recorded"
        if not isinstance(checks, list) or any(not isinstance(c, dict) for c in checks):
            return "blocked", "CI returned an invalid check list; no approval recorded"
        if done.returncode not in (0, 1, 8):
            return "blocked", "CI checks are unavailable: " + (done.stderr or done.stdout).strip()[-1000:]
        waited = time.time() - start
        if checks and not any(c.get("bucket") == "pending" for c in checks):
            break
        if waited > minutes * 60 or (not checks and waited > NO_CHECKS_WAIT):
            return "blocked", "CI still running at handoff" if checks else "no CI checks reported"
        time.sleep(POLL)
    failed = [c for c in checks if c.get("bucket") == "fail"]
    if any(c.get("bucket") not in ("pass", "fail") for c in checks):
        return "blocked", "CI contains cancelled, skipped or unrecognized results; no approval recorded"
    if not failed:
        return "ok", f"CI passed ({len(checks)} checks)"
    logs = []
    for c in failed[:3]:
        run = re.search(r"/runs/(\d+)", c.get("link") or "")
        tail = _gh(ws, "run", "view", run.group(1), "--log-failed").stdout[-3000:] if run else ""
        logs.append(f"{c.get('name')}: {c.get('link')}\n{tail}".strip())
    return "fail", "CI failed on the pull request:\n\n" + "\n\n".join(logs)


def ready(ws: Path, state: dict) -> Optional[str]:
    """Mark the pull request ready for review; the error, if any."""
    if not state.get("pr_url"):
        return None
    done = _gh(ws, "pr", "ready", state["pr_url"])
    return None if done.returncode == 0 else (done.stderr or done.stdout).strip()[-500:]
