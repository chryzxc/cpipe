"""cpipe's facts: check results, comparisons, the step checklist and the run's verdict.

It reads files and prints data and verdicts. It never starts a model, picks a step or stops a run: the model runs
every step and acts on what this prints (skills/run/SKILL.md). Standard library only.

  python3 cpipe.py results <out.json> <junit|jest|tsc|eslint|lines> <file>... [--root <dir>]
  python3 cpipe.py compare <run dir> <check>
  python3 cpipe.py track <run dir> add <step> <text> | set <id> <open|done|skip|user> [<evidence>] | open [<step>]
  python3 cpipe.py gate <run dir>
  python3 cpipe.py report <run dir>
  python3 cpipe.py active <run dir> [--done]
  python3 cpipe.py chat
"""

import json
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

STATUSES = ("open", "done", "skip", "user")
LINE_COL = re.compile(r"[:(]\d+(?:[:,]\d+)?\)?(?=[:\s]|$)")


def _rel(path, root):
    """A tree-independent id part: the base and delivery worktrees live in different folders."""
    path = str(path or "")
    try:
        return os.path.relpath(path, root) if os.path.isabs(path) else path
    except ValueError:
        return path


def _junit(files, root):
    out = {"passed": set(), "failed": set(), "skipped": set(), "errors": set()}
    for f in files:
        for case in ET.parse(f).iter("testcase"):
            where = _rel(case.get("file") or case.get("classname") or "", root)
            name = f"{where}::{case.get('name', '')}"
            kids = {child.tag for child in case}
            state = ("failed" if kids & {"failure", "error"} else "skipped" if "skipped" in kids else "passed")
            out[state].add(name)
    return out


def _jest(files, root):
    out = {"passed": set(), "failed": set(), "skipped": set(), "errors": set()}
    for f in files:
        data = json.loads(Path(f).read_text())
        for suite in data.get("testResults", []):
            where = _rel(suite.get("name") or suite.get("testFilePath"), root)
            tests = suite.get("assertionResults") or suite.get("testResults") or []
            for t in tests:
                name = f"{where}::{' '.join([*t.get('ancestorTitles', []), t.get('title', '')]).strip()}"
                status = t.get("status")
                out["passed" if status == "passed" else "failed" if status == "failed" else "skipped"].add(name)
            if suite.get("status") == "failed" and not any(t.get("status") == "failed" for t in tests):
                out["errors"].add(f"{where}::<suite failed to run>")
    return out


def _tsc(files, root):
    pattern = re.compile(r"^(.+?)(?:\(\d+,\d+\)|:\d+:\d+)\s*[-:]\s*error\s+(TS\d+):\s*(.*)$")
    errors = set()
    for f in files:
        for line in Path(f).read_text(errors="replace").splitlines():
            m = pattern.match(line.strip())
            if m:
                errors.add(f"{_rel(m[1], root)}:{m[2]}:{m[3].strip()}")
    return {"passed": set(), "failed": set(), "skipped": set(), "errors": errors}


def _eslint(files, root):
    errors = set()
    for f in files:
        for entry in json.loads(Path(f).read_text()):
            for m in entry.get("messages", []):
                if m.get("severity", 2) >= 2:
                    errors.add(f"{_rel(entry.get('filePath'), root)}:{m.get('ruleId') or 'parse'}:{m.get('message')}")
    return {"passed": set(), "failed": set(), "skipped": set(), "errors": errors}


def _lines(files, root):
    """One error per line, any tool: the line and column are dropped, since they move when code is added."""
    errors = set()
    for f in files:
        for line in Path(f).read_text(errors="replace").splitlines():
            line = line.strip()
            if line:
                path, _, rest = line.partition(":")
                errors.add(LINE_COL.sub("", f"{_rel(path, root)}:{rest}" if rest else line))
    return {"passed": set(), "failed": set(), "skipped": set(), "errors": errors}


PARSERS = {"junit": ("tests", _junit), "jest": ("tests", _jest), "tsc": ("static", _tsc),
           "eslint": ("static", _eslint), "lines": ("static", _lines)}


def results(out, fmt, files, root):
    kind, parse = PARSERS[fmt]
    missing = [f for f in files if not Path(f).is_file()]
    if missing:
        raise SystemExit(f"no results file {', '.join(missing)}: run the check again with its reporter writing it")
    try:
        data = parse(files, root)
    except (ET.ParseError, ValueError) as exc:
        raise SystemExit(f"results file cut off or unreadable ({exc}): run the check again with its reporter")
    record = {"kind": kind, **{k: sorted(v) for k, v in data.items()}}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(record, indent=1) + "\n")
    return record


def _load(path):
    return json.loads(path.read_text()) if path.is_file() else None


def _bad(r):
    return set(r["failed"]) | set(r["errors"]) if r else set()


def compare(run_dir, check):
    """Every failing name on the delivery is new, baseline, flaky or still to run alone: never undecided."""
    d = Path(run_dir) / "results"
    base, head = _load(d / f"{check}.base.json"), _load(d / f"{check}.head.json")
    out = {k: [] for k in ("new", "baseline", "fixed", "flaky", "rerun-head", "rerun-base")}
    if head is None:
        out["rerun-head"] = ["*"]
        return out
    alone = {tree: [r for p in sorted(d.glob(f"{check}.{tree}-alone*.json")) if (r := _load(p))]
             for tree in ("base", "head")}
    base_bad = _bad(base) | {n for r in alone["base"] for n in _bad(r)}
    base_ok = set(base["passed"]) if base else set()
    base_ok |= {n for r in alone["base"] for n in r["passed"]}
    head_alone_ok = {n for r in alone["head"] for n in r["passed"]}
    head_alone_bad = {n for r in alone["head"] for n in _bad(r)}
    out["fixed"] = sorted(_bad(base) & set(head["passed"]))
    static = head.get("kind") == "static"
    for name in sorted(_bad(head)):
        if name in base_bad:
            out["baseline"].append(name)
        elif not static and name in head_alone_ok:
            out["flaky"].append(name)
        elif not static and name not in head_alone_bad:
            out["rerun-head"].append(name)
        elif name in base_ok or (static and base is not None):
            out["new"].append(name)
        else:
            out["rerun-base"].append(name)
    return out


def _track_path(run_dir):
    return Path(run_dir) / "track.json"


def _items(run_dir):
    return _load(_track_path(run_dir)) or []


def track(run_dir, action, args):
    items = _items(run_dir)
    if action == "add":
        step, text = args[0], " ".join(args[1:])
        same = [i for i in items if i["step"] == step and i["text"] == text]
        if same:
            return same[0]["id"]
        n = 1 + sum(i["step"] == step for i in items)
        items.append({"id": f"{step}-{n}", "step": step, "text": text, "status": "open", "evidence": ""})
        result = items[-1]["id"]
    elif action == "set":
        ident, status, evidence = args[0], args[1], " ".join(args[2:]).strip()
        if status not in STATUSES:
            raise SystemExit(f"status is one of {', '.join(STATUSES)}")
        if status != "open" and not evidence:
            raise SystemExit(f"{ident}: {status} needs its evidence (a result, a file, a count) or its reason")
        match = [i for i in items if i["id"] == ident]
        if not match:
            raise SystemExit(f"no item {ident}")
        match[0].update(status=status, evidence=evidence)
        result = f"{ident} {status}"
    else:
        step = args[0] if args else None
        return "\n".join(f"{i['id']} [{i['status']}] {i['text']}" for i in items
                         if i["status"] == "open" and (step is None or i["step"] == step)) or "nothing open"
    _track_path(run_dir).write_text(json.dumps(items, indent=1) + "\n")
    return result


def gate(run_dir):
    """FIX before WAIT before NEEDS-USER: the loop's own work first. PASS only when nothing is left."""
    fix, wait, user = [], [], []
    for i in _items(run_dir):
        if i["status"] == "open" or (i["status"] != "user" and not i["evidence"]):
            fix.append(f"{i['id']} {i['text']}")
        elif i["status"] == "user":
            user.append(f"{i['id']} {i['evidence']}")
    d = Path(run_dir) / "results"
    for p in sorted(d.glob("*.head.json")):
        check = p.name[: -len(".head.json")]
        c = compare(run_dir, check)
        fix += [f"{check}: new failure {n}" for n in c["new"]]
        fix += [f"{check}: run {n} alone on the delivery (results {check}.head-alone.json)" for n in c["rerun-head"]]
        fix += [f"{check}: run {n} on the base (results {check}.base-alone.json)" for n in c["rerun-base"]]
    for ci in _load(d / "ci.json") or []:
        bucket = ci.get("bucket") or ci.get("state", "").lower()
        if bucket in ("fail", "failure", "cancel", "cancelled"):
            fix.append(f"CI {ci.get('name')}: {bucket}")
        elif bucket in ("pending", "in_progress", "queued"):
            wait.append(f"CI {ci.get('name')}")
    if fix:
        return "FIX: " + " | ".join(fix)
    if wait:
        return "WAIT: " + ", ".join(wait)
    if user:
        return "NEEDS-USER: " + " | ".join(user)
    return "PASS: every item done with evidence, no new failure, CI green"


def report(run_dir):
    """The report's skeleton with the facts filled in; the model writes only the <...> cells (steps/report.md)."""
    verdict = gate(run_dir)
    items = _items(run_dir)
    status = ("✅ Done: <goal>" if verdict.startswith("PASS")
              else "⚠️ Done except " + verdict.split(": ", 1)[1] + ": <goal>" if verdict.startswith("NEEDS-USER")
              else f"not ready to report, the loop has work: {verdict}")
    checks = []
    for p in sorted((Path(run_dir) / "results").glob("*.head.json")):
        check = p.name[: -len(".head.json")]
        c, head = compare(run_dir, check), _load(p)
        mark = "✅" if not (c["new"] or c["rerun-head"] or c["rerun-base"]) else "⚠️"
        checks.append(f"{mark} {check}: {len(head['passed'])} pass, {len(c['baseline'])} baseline, "
                      f"{len(c['new'])} new, {len(c['flaky'])} flaky, {len(c['fixed'])} fixed")
    attention = [f"| {i['text']} | {i['evidence']} | <recommended> |" for i in items if i["status"] in ("user", "skip")]
    parts = [status, "", "<Summary>", "", "## Test it", "App: <URL or command, worktree>",
             "| # | What to check | How | Expected |", "", "## Before and after",
             "| # | What | Before | After | QA |", ""]
    if attention:
        parts += ["## Needs attention", "| What | Why it matters | Recommended |", *attention, ""]
    parts += ["## Proof", *checks, "<Tests, QA, Review and Steps lines>", "", "## Where to look", "| Where | Why |", "",
              "Risk: <blast radius> · Rollback: <how> · Confidence: <what it rests on; the weak spot>", "",
              "## Approach", "| Choice | Why | Instead of |", "", "## Run graph", "<the flow chart>", "",
              "Cost: <minutes> · <tokens> (<cached share>) · <who did each step>", "Details: <one line>"]
    return "\n".join(parts)


def hermes_root():
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home.parent.parent if home.parent.name == "profiles" else home


def active(run_dir, done=False):
    """Register this chat's run so the pinned rules find it after a compaction (__init__.py, _pinned)."""
    sid = os.environ.get("HERMES_SESSION_ID", "")
    if not sid:
        raise SystemExit("no HERMES_SESSION_ID in this shell: the pinned rules come from CPIPE_RUN instead")
    path = hermes_root() / "cpipe" / "active" / f"{sid}.json"
    if done:
        path.unlink(missing_ok=True)
        return f"run closed for {sid}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_dir": str(Path(run_dir).resolve())}) + "\n")
    return f"run registered for {sid}"


def chat():
    """The `hermes chat` flags that run a step on this chat's own profile, model and provider."""
    sid = os.environ.get("HERMES_SESSION_ID", "")
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    if not sid:
        raise SystemExit("no HERMES_SESSION_ID in this shell: run the step without -p/-m (the default profile)")
    db = sqlite3.connect(f"file:{home / 'state.db'}?mode=ro", uri=True)
    try:
        row = db.execute("SELECT profile_name, model, billing_provider, model_config FROM sessions WHERE id = ?",
                         (sid,)).fetchone()
    finally:
        db.close()
    if not row:
        raise SystemExit(f"session {sid} not found in {home / 'state.db'}")
    profile, model, provider, config = row
    try:
        provider = json.loads(config or "{}").get("provider") or provider  # a /model switch stores it here
    except ValueError:
        pass
    flags = [f"-p {profile}"] if profile else []
    flags += [f"-m {model}"] if model else []
    flags += [f"--provider {provider}"] if provider else []
    return " ".join(flags)


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    cmd, args = argv[1], argv[2:]
    if cmd == "results":
        root = os.getcwd()
        if "--root" in args:
            i = args.index("--root")
            root, args = args[i + 1], args[:i] + args[i + 2:]
        r = results(args[0], args[1], args[2:], root)
        print(f"{args[0]}: {len(r['passed'])} passed, {len(r['failed'])} failed, {len(r['skipped'])} skipped, "
              f"{len(r['errors'])} errors")
    elif cmd == "compare":
        for k, v in compare(args[0], args[1]).items():
            print(f"{k}: {', '.join(v) or '-'}")
    elif cmd == "track":
        print(track(args[0], args[1], args[2:]))
    elif cmd == "gate":
        print(gate(args[0]))
    elif cmd == "report":
        print(report(args[0]))
    elif cmd == "active":
        print(active(args[0], "--done" in args))
    elif cmd == "chat":
        print(chat())
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
