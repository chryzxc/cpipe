"""cpipe's facts: check results, comparisons, the step checklist and the run's verdict.

It reads files and prints data and verdicts. It never starts a model, picks a step or stops a run: the model runs
every step and acts on what this prints (skills/run/SKILL.md). Standard library only.

  python3 cpipe.py results <out.json> <junit|jest|tsc|eslint|lines> <file>... [--root <dir>]
  python3 cpipe.py compare <run dir> <check>
  python3 cpipe.py track <run dir> add <step> <text> | set <id> <open|done|skip|user> [<evidence>, for user `<kind>: …`, a question `answer: R2: …`] | open [<step>]
  python3 cpipe.py gate <run dir>
  python3 cpipe.py ci <run dir> <pull request> [--every <seconds>]
  python3 cpipe.py report <run dir> [--check]
  python3 cpipe.py active <run dir> [--done]
  python3 cpipe.py chat
  python3 cpipe.py brief <run dir> <worktree> <role> <round> [--resume] [--card] [--bare]
  python3 cpipe.py rounds <run dir>
  python3 cpipe.py lanes <batch dir> [--at-once <n>] [--report <lane>]
  python3 cpipe.py step <lane dir>
  python3 cpipe.py cost <run dir> <worktree> [--start]
  python3 cpipe.py port <source commit or from..to> <base> [--repo <source repository>]
"""

import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

STATUSES = ("open", "done", "skip", "user")
# What only the user may do (SKILL.md, Unattended): how the code should behave, or which fix, is never one
USER_KINDS = ("credentials", "destructive", "external", "approval", "answer")
NOT_USER = ("{} is the loop's work, not the user's: `user` is only for " + ", ".join(USER_KINDS) +
            " (evidence `<kind>: …`); a choice between fixes or scope is decided by the run, but two of the "
            "user's own lines that cannot both hold are theirs to settle: ask it as a spec question "
            "(`answer: R2: …`, spec.md, Reconcile) and work on the rest")


# What `skip` may mean: the item does not exist in this change, or this machine cannot run it; never "too big" or
# "out of scope", and never a user's instruction (only the user drops one)
SKIP_KINDS = ("n/a", "machine")
NOT_SKIP = ("{} cannot be skipped: `skip` is only for " + ", ".join(SKIP_KINDS) + " (evidence `<kind>: …`: "
            "it does not exist in this change, or this machine cannot run it); anything else is the loop's work")
NOT_SKIP_TOLD = "{} is the user's instruction: only the user drops it, so it is done or still the loop's work"


def _kind(evidence, kinds):
    return evidence.split(":", 1)[0].strip().lower() in kinds


def _for_user(evidence):
    return _kind(evidence, USER_KINDS)


# A question for the user names the spec line it settles (spec.md, Questions): `answer: R2: <question>`
ASKS = re.compile(r"^answer:\s*([RC]\d+)\b", re.I)
NOT_ASK = ("{} is not a spec question: a question names the spec line it settles (`answer: R2: …`, the line in "
           "spec.md); any other choice is the run's")
SPEC_LINE = re.compile(r"^\s*-\s*\**([RCDNIL]\d+)\**\s*:(.*)$")
TRACED = "RCDL"  # the lines the delivery proves: requirements, constraints, the agreed design, what it is like
UNTRACED = "spec {} has no checklist item: `track add plan \"{}: <the line>\"`, closed with its proof"
UNLIKE = ("spec has no L line: name the existing feature of the same kind this change is like and what it matches "
          "(`- L1: <output> · like: <file:line> · matches: <fields, format, template, whose time zone>`), or "
          "`- L1: none · why: <nothing of its kind exists>` (spec.md, Like)")
UNSOURCED = "spec {} has no source in the user's words (` · from: \"<their exact words>\"`); a reading of yours is an I line"


def _spec(run_dir):
    """spec.md's lines by id: R requirements, C constraints, D the agreed design, N non-goals, I inferences, the run's own readings."""
    try:
        text = (Path(run_dir) / "spec.md").read_text()
    except OSError:
        return {}
    return {m.group(1).upper(): m.group(2).strip() for m in map(SPEC_LINE.match, text.splitlines()) if m}


def _drafting(run_dir):
    try:
        return bool(re.search(r"(?m)^Status: drafting", (Path(run_dir) / "spec.md").read_text()))
    except OSError:
        return False


def _traced(items, ident):
    """The checklist items that prove a spec line: their text starts with its id."""
    return [i for i in items if re.match(rf"\**{ident}\b", i["text"])]


def _asks(run_dir, evidence):
    m = ASKS.match(evidence.strip())
    return bool(m) and m.group(1).upper() in _spec(run_dir)
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


def _error_kind(name):
    """A static error without its file, and with relative import paths reduced to their extension."""
    rest = name.split(":", 1)[1] if ":" in name else name
    return re.sub(r"'\.{1,2}/[^']*?(\.\w+)?'", lambda m: f"'*{m[1] or ''}'", rest)


def compare(run_dir, check):
    """Every failing name on the delivery is new, baseline, flaky or still to run alone: never undecided."""
    d = Path(run_dir) / "results"
    base, head = _load(d / f"{check}.base.json"), _load(d / f"{check}.head.json")
    out = {k: [] for k in ("new", "baseline", "fixed", "flaky", "rerun-head", "rerun-base")}
    if head is None:
        out["rerun-head"] = ["*"]
        return out
    # Only records `results` wrote: a reporter's raw output saved next to them matches the glob too
    alone = {tree: [r for p in sorted(d.glob(f"{check}.{tree}-alone*.json")) if "kind" in (r := _load(p) or {})]
             for tree in ("base", "head")}
    base_bad = _bad(base) | {n for r in alone["base"] for n in _bad(r)}
    base_ok = set(base["passed"]) if base else set()
    base_ok |= {n for r in alone["base"] for n in r["passed"]}
    # Run on the base and still not there: the delivery added it, so it can only be new
    base_seen = {n for r in [base, *alone["base"]] if r for k in ("passed", "failed", "skipped", "errors") for n in r[k]}
    head_alone_ok = {n for r in alone["head"] for n in r["passed"]}
    head_alone_bad = {n for r in alone["head"] for n in _bad(r)}
    out["fixed"] = sorted(_bad(base) & set(head["passed"]))
    static = head.get("kind") == "static"
    # A static error the base has word for word in another file is the project's setup (test globals, untyped
    # imports), not this change: a new file gets the same error every old one has
    base_kinds = {_error_kind(n) for n in base_bad} if static else set()
    for name in sorted(_bad(head)):
        if name in base_bad or (static and _error_kind(name) in base_kinds):
            out["baseline"].append(name)
        elif not static and name in head_alone_ok:
            out["flaky"].append(name)
        elif not static and name not in head_alone_bad:
            out["rerun-head"].append(name)
        elif name in base_ok or (static and base is not None) or (alone["base"] and name not in base_seen):
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
        if status == "user" and not _for_user(evidence):
            raise SystemExit(NOT_USER.format(ident))
        if status == "user" and _kind(evidence, ("answer",)) and not _asks(run_dir, evidence):
            raise SystemExit(NOT_ASK.format(ident))
        if status == "skip" and not _kind(evidence, SKIP_KINDS):
            raise SystemExit(NOT_SKIP.format(ident))
        match = [i for i in items if i["id"] == ident]
        if not match:
            raise SystemExit(f"no item {ident}")
        if status == "skip" and _fold(match[0]["text"]) in map(_fold, _told(run_dir)):
            raise SystemExit(NOT_SKIP_TOLD.format(ident))
        match[0].update(status=status, evidence=evidence)
        result = f"{ident} {status}"
    else:
        step = args[0] if args else None
        return "\n".join(f"{i['id']} [{i['status']}] {i['text']}" for i in items
                         if i["status"] == "open" and (step is None or i["step"] == step)) or "nothing open"
    _track_path(run_dir).write_text(json.dumps(items, indent=1) + "\n")
    return result


CI_FAILED = {"fail", "failure", "cancel", "cancelled", "timed_out", "startup_failure", "action_required"}


def _ci_bucket(ci):
    """`gh pr checks` gives a bucket; the check-runs API a conclusion, or a status while it runs."""
    return (ci.get("bucket") or ci.get("conclusion") or ci.get("state") or ci.get("status") or "").lower()


CI_RUNNING = ("pending", "in_progress", "queued")
CI_TRIES = 10  # ponytail: consecutive failed reads before the wait says so; a GitHub blip (401, 502) passes well within


def ci(run_dir, pr, every=30, tries=CI_TRIES, run=subprocess.run, sleep=time.sleep):
    """Waits for the pull request's CI, saving results/ci.json on every read. `gh pr checks --watch` ends on the
    first failed request (a passing 401 or 502 from GitHub), so this reads again instead, and gives up only after
    `tries` failed reads in a row."""
    out, failed, err = Path(run_dir) / "results" / "ci.json", 0, ""
    out.parent.mkdir(parents=True, exist_ok=True)
    while True:
        r = run(["gh", "pr", "checks", str(pr), "--json", "name,bucket"], capture_output=True, text=True)
        try:
            checks = json.loads(r.stdout)
        except ValueError:
            checks, err = None, (r.stderr or r.stdout).strip()
        if checks is None:
            failed += 1
            if failed < tries:
                sleep(every)
                continue
            if "no checks reported" not in err:  # none yet right after a push, or none at all once the tries pass
                return 1, f"CI unreadable after {tries} tries, last: {err[-300:]}"
            checks = []
        failed = 0
        out.write_text(json.dumps(checks, indent=1) + "\n")
        running = [c["name"] for c in checks if _ci_bucket(c) in CI_RUNNING]
        if not running:
            return 0, "CI done: " + (", ".join(f"{c['name']} {_ci_bucket(c)}" for c in checks) or "no checks")
        sleep(every)


def gate(run_dir, sid=None):
    """SPEC while the spec is drafted, then FIX before WAIT before ASK before NEEDS-USER: the loop's own work first. PASS only when nothing is left."""
    if _drafting(run_dir):  # --spec: the user and the planner are still working out what to build
        return "SPEC: the plan profile is working out the spec with the user (spec.md, --spec)"
    fix, wait, ask, user = [], [], [], []
    items = _items(run_dir)
    told = set(map(_fold, _told(run_dir)))
    if items and not (Path(run_dir) / "spec.md").exists():  # a run from before spec.md: nothing can be asked
        fix.append("no spec.md: write it from the user's words (spec.md, Format), so a conflict between their "
                   "lines can be asked instead of looped on")
    if not items:  # a run from before track.json, or one that lost it: nothing is proven yet
        fix.append("no checklist: add each step's Done when, the user's instructions and the merge conditions "
                   "with track add, closing each with its evidence")
    for i in items:
        if i.get("step") == "instruction" and _launch_text(i["text"]):  # cpipe's run text, tracked as if the user's
            continue
        if i["status"] == "open" or (i["status"] != "user" and not i["evidence"]):
            fix.append(f"{i['id']} {i['text']}")
        elif i["status"] == "user" and not _for_user(i["evidence"]):
            fix.append(f"{NOT_USER.format(i['id'])}: {i['text']}")
        elif i["status"] == "skip" and not _kind(i["evidence"], SKIP_KINDS):
            fix.append(f"{NOT_SKIP.format(i['id'])}: {i['text']}")
        elif i["status"] == "skip" and _fold(i["text"]) in told:
            fix.append(f"{NOT_SKIP_TOLD.format(i['id'])}: {i['text']}")
        elif i["status"] == "user" and _kind(i["evidence"], ("answer",)):
            if _asks(run_dir, i["evidence"]):
                ask.append(i["evidence"].split(":", 1)[1].strip())
            else:
                fix.append(NOT_ASK.format(i["id"]))
        elif i["status"] == "user":
            user.append(f"{i['id']} {i['evidence']}")
    fix += [UNSAID.format(line) for line in unsaid(run_dir, sid)]
    texts = [_fold(i["text"]) for i in items]
    fix += [UNTRACKED.format(line) for line in told if not any(t.startswith(line) for t in texts)]
    fix += [UNSOURCED.format(ident) for ident in unsourced(run_dir, sid)]
    spec = _spec(run_dir)
    fix += [UNTRACED.format(ident, ident) for ident in spec if ident[0] in TRACED and not _traced(items, ident)]
    if any(k[0] == "R" for k in spec) and not any(k[0] == "L" for k in spec):
        fix.append(UNLIKE)
    d = Path(run_dir) / "results"
    checks = sorted({p.name.rsplit(".", 2)[0] for p in [*d.glob("*.base.json"), *d.glob("*.head.json")]})
    if not checks:
        fix.append("no check results: save each check's results on the base and the delivery with results "
                   "(results/<check>.base.json, results/<check>.head.json)")
    for check in checks:
        c = compare(run_dir, check)
        fix += [f"{check}: new failure {n}" for n in c["new"]]
        fix += [f"{check}: run {n} alone on the delivery (results {check}.head-alone.json)" for n in c["rerun-head"]]
        fix += [f"{check}: run {n} on the base (results {check}.base-alone.json)" for n in c["rerun-base"]]
    # A job that fails the same way on the base commit is the base's, as with any check
    base_failed = {ci.get("name") for ci in _load(d / "ci-base.json") or [] if _ci_bucket(ci) in CI_FAILED}
    for ci in _load(d / "ci.json") or []:
        bucket = _ci_bucket(ci)
        if bucket in CI_FAILED and ci.get("name") not in base_failed:
            fix.append(f"CI {ci.get('name')}: {bucket}")
        elif bucket in CI_RUNNING:
            wait.append(f"CI {ci.get('name')}")
    if fix:
        return "FIX: " + " | ".join(fix)
    if wait:
        return "WAIT: " + ", ".join(wait)
    if ask:  # every other item is done: the run waits for the user's answer, never reports around it
        return "ASK: " + " | ".join(ask)
    if user:
        return "NEEDS-USER: " + " | ".join(user)
    return "PASS: every item done with evidence, no new failure, CI green"


def report(run_dir):
    """The report's skeleton with the facts filled in; the model writes only the <...> cells (steps/report.md)."""
    verdict = gate(run_dir)
    items = _items(run_dir)
    status = ("✅ Done: <goal>" if verdict.startswith("PASS")
              else "⚠️ Done except " + verdict.split(": ", 1)[1] + ": <goal>" if verdict.startswith("NEEDS-USER")
              else f"not ready to report, the run waits on your answer: {verdict[5:]}" if verdict.startswith("ASK")
              else f"not ready to report, the loop has work: {verdict}")
    checks = []
    for p in sorted((Path(run_dir) / "results").glob("*.head.json")):
        check = p.name[: -len(".head.json")]
        c, head = compare(run_dir, check), _load(p)
        mark = "✅" if not (c["new"] or c["rerun-head"] or c["rerun-base"]) else "⚠️"
        checks.append(f"{mark} {check}: {len(head['passed'])} pass, {len(c['baseline'])} baseline, "
                      f"{len(c['new'])} new, {len(c['flaky'])} flaky, {len(c['fixed'])} fixed")
    for ident in (k for k in _spec(run_dir) if k[0] in TRACED):  # each spec line with what proves it
        for i in _traced(items, ident):
            mark = "✅" if i["status"] == "done" else "⚠️"
            checks.append(f"{mark} {i['text']} · {i['evidence'] or 'open'}")
    attention = [f"| {i['text']} | {i['evidence']} | <recommended> |" for i in items if i["status"] in ("user", "skip")]
    parts = [status, "", "<Summary>", "", "## Test it", "App: <URL or command, worktree>",
             "| # | What to check | How | Expected |", "", "## Before and after",
             "| # | What | Before | After | QA |", ""]
    if attention:
        parts += ["## Needs attention", "| What | Why it matters | Recommended |", *attention, ""]
    parts += ["## Proof", *checks, "<Tests, QA, Review and Steps lines>", "", "## Where to look", "| Where | Why |", "",
              "Risk: <blast radius> · Rollback: <how> · Confidence: <what it rests on; the weak spot>", "",
              "## Approach", "| Choice | Why | Instead of |", "", "## Run graph", "<the flow chart>", "",
              "## Cost", "Time: <minutes> min", "<the table `cost` prints, each Who with its steps>", "",
              "Details: <one line>"]
    return "\n".join(parts)


REPORT_PARTS = ("## Test it", "## Proof", "## Run graph", "Risk: ", "## Cost", "Details: ")


def check_report(run_dir):
    """Whether <run dir>/report.md is the report: the skeleton's status and Proof facts kept, every <…> written."""
    path = Path(run_dir) / "report.md"
    skeleton = report(run_dir)
    if skeleton.startswith("not ready"):
        return "FIX: " + skeleton
    try:
        text = path.read_text()
    except OSError:
        return f"FIX: write the report to {path}, starting from `report {run_dir}`"
    # A review's verdict line stands for the status line (steps/report.md, Status line).
    passed = skeleton.startswith("✅")
    status = ("✅ Done", "✅ Approved", "⚠️ Changes needed") if passed else ("⚠️ Done except", "⚠️ Changes needed")
    problems = [] if text.lstrip().startswith(status) else [f"the first line is not the status line `{status[0]}…`"]
    problems += [f"missing `{part.strip()}`" for part in REPORT_PARTS
                 if not re.search(rf"^{re.escape(part)}", text, re.M)]
    proof = skeleton.split("## Proof\n", 1)[1].split("\n<", 1)[0].splitlines()
    problems += [f"Proof line changed or missing: {line}" for line in proof if line not in text]
    problems += [f"unwritten cell {cell}" for cell in sorted(set(re.findall(r"<[^<>\n]+>", skeleton))) if cell in text]
    if problems:
        return "FIX: " + "; ".join(problems)
    # The run ends with its report: close it for this chat, so a later message is the user's own work.
    closed = "; the run is closed for this chat" if os.environ.get("HERMES_SESSION_ID") else ""
    if closed:
        active(run_dir, done=True)
    return f"PASS: post {path} word for word as the final reply{closed}"


def hermes_root():
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home.parent.parent if home.parent.name == "profiles" else home


def _tokens(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k"


COUNTERS = ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "api_call_count")


def _usage_members(run_dir, coordinators=()):
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    profile = home.name if home.parent.name == "profiles" else "default"
    sid = os.environ.get("HERMES_SESSION_ID", "")
    chats = Path(run_dir) / "chats"
    ids = chats.read_text().split() if chats.is_file() else []
    if sid:
        ids.append(sid)
    enrolled = {tuple(pair) for pair in coordinators}
    known = {ident for _, ident in enrolled}
    enrolled.update((profile, ident) for ident in ids if ident == sid or ident not in known)
    members = set(enrolled)
    warnings, fresh = [], set()
    for log in (Path(run_dir) / "logs").glob("*.txt"):
        try:
            text = log.read_text(errors="replace")
            profiles = re.findall(r"^cpipe_profile: ([\w-]+)\s*$", text, re.M)
            sessions = re.findall(r"^session_id: (\S+)\s*$", text, re.M)
            if profiles and profiles[-1] != "unknown" and sessions:
                pair = (profiles[-1], sessions[-1])
                members.add(pair)
                if re.search(r"^cpipe_resume: new\s*$", text, re.M):
                    fresh.add(pair)
            else:
                warnings.append("role launch identity incomplete")
        except OSError:
            warnings.append("role launch identity unreadable")
    return members, enrolled, warnings, fresh


def _usage_read(tree, members):
    """Read-only, transaction-consistent counters per store; candidates are not run membership."""
    root = hermes_root()
    stores, warnings = {}, []
    for path in [root / "state.db", *sorted(root.glob("profiles/*/state.db"))]:
        profile = "default" if path == root / "state.db" else path.parent.name
        stamp = time.time()
        try:
            db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
            try:
                db.execute("BEGIN")
                columns = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
                if not {"id", "cwd", "started_at", "model"} <= columns:
                    raise sqlite3.DatabaseError("unsupported session schema")
                missing = [name for name in COUNTERS if name not in columns]
                names = ("id", "cwd", "started_at", "model", "parent_session_id", *COUNTERS)
                fields = ", ".join(name if name in columns else "NULL AS " + name for name in names)
                ids = [ident for who, ident in members if who == profile]
                where = "cwd = ? OR substr(cwd, 1, ?) = ?"
                args = [tree, len(tree) + 1, tree + "/"]
                if ids:
                    where += " OR id IN (" + ",".join("?" for _ in ids) + ")"
                    args += ids
                found = db.execute("SELECT " + fields + " FROM sessions WHERE " + where, args).fetchall()
                ledger_columns = {r[1] for r in db.execute("PRAGMA table_info(session_model_usage)")}
                ledger_ok = {"session_id", "model", "task", "billing_provider", *COUNTERS} <= ledger_columns
                sessions = {}
                for ident, cwd, started, model, parent, *counts in found:
                    rows = {}
                    if ledger_ok:
                        for m, provider, task, *values in db.execute(
                                "SELECT model,billing_provider,task," + ",".join(COUNTERS) +
                                " FROM session_model_usage WHERE session_id = ?", (ident,)):
                            key = json.dumps([m or "?", provider or "?", task or "main"])
                            previous = rows.setdefault(key, [0] * len(COUNTERS))
                            for i, value in enumerate(values):
                                previous[i] += int(value or 0)
                    source = "ledger" if rows else "session"
                    if not rows:
                        rows[json.dumps([model or "?", "unknown", "unseparated"])] = [int(n or 0) for n in counts]
                    sessions[ident] = {"started": started or 0, "parent": parent, "source": source,
                                       "rows": rows, "missing": missing if source == "session" else []}
                stores[profile] = {"captured": stamp, "sessions": sessions}
            finally:
                db.close()
        except (sqlite3.Error, ValueError, OSError, TypeError):
            warnings.append(profile + ": store unavailable or unsupported")
    return stores, warnings


def _usage_snapshot(path, tree):
    data = json.loads(path.read_text())
    if data["version"] != 2 or data["worktree"] != tree:
        raise ValueError("capture belongs to a different worktree or schema")
    if not isinstance(data["stores"], dict) or not isinstance(data["coordinators"], list):
        raise ValueError("invalid capture")
    import math
    def timestamp(value):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 253402300799:
            raise ValueError("invalid timestamp")
    timestamp(data["captured"])
    if (not isinstance(data["errors"], list) or any(not isinstance(s, str) for s in data["errors"]) or
            any(not isinstance(pair, list) or len(pair) != 2 or
                any(not isinstance(s, str) for s in pair) for pair in data["coordinators"])):
        raise ValueError("invalid identities or coverage")
    for store in data["stores"].values():
        timestamp(store["captured"])
        for session in store["sessions"].values():
            timestamp(session["captured"])
            if session["source"] not in ("ledger", "session") or not isinstance(session["missing"], list):
                raise ValueError("invalid counter source")
            if any(name not in COUNTERS for name in session["missing"]):
                raise ValueError("invalid missing counter")
            for key, values in session["rows"].items():
                labels = json.loads(key)
                if not isinstance(labels, list) or len(labels) != 3 or any(not isinstance(s, str) for s in labels) or len(values) != len(COUNTERS):
                    raise ValueError("invalid counter row")
                if any(not isinstance(n, int) or n < 0 for n in values):
                    raise ValueError("invalid counters")
    return data


def cost(run_dir, worktree, start=False):
    """Read usage, or explicitly capture counters. Never starts models or changes their settings."""
    tree = str(Path(worktree).resolve())
    snapshot = Path(run_dir) / "usage-start.json"
    data = None
    if snapshot.exists():
        try:
            data = _usage_snapshot(snapshot, tree)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return "Partial coverage: capture is unreadable or belongs to a different worktree/schema; preserved unchanged"
    if not start and data is None:
        return _legacy_cost(run_dir, worktree) + "\nCoverage: legacy/lifetime selection, not exact run usage; capture with cost --start for future work"
    members, coordinators, warnings, fresh = _usage_members(run_dir, data["coordinators"] if data else ())
    current, errors = _usage_read(tree, members)
    warnings += errors
    if start:
        import tempfile
        before = json.dumps(data, sort_keys=True) if data else ""
        if data is None:
            data = {"version": 2, "worktree": tree, "captured": min((s["captured"] for s in current.values()), default=time.time()), "stores": {},
                    "coordinators": [], "errors": []}
        data["coordinators"] = sorted([list(pair) for pair in set(map(tuple, data["coordinators"])) | coordinators])
        for profile, store in current.items():
            baseline = data["stores"].setdefault(profile, {"captured": store["captured"], "sessions": {}})
            for ident, session in store["sessions"].items():
                if ident not in baseline["sessions"]:
                    captured = dict(session, captured=store["captured"])
                    if ((profile, ident) in fresh and not session["parent"] and
                            session["started"] >= baseline["captured"]):
                        captured.update(rows={}, captured=baseline["captured"])
                    baseline["sessions"][ident] = captured
        data["errors"] = sorted(set(data["errors"] + warnings))
        if before != json.dumps(data, sort_keys=True):
            fd, temp = tempfile.mkstemp(prefix=".usage-start-", dir=Path(run_dir))
            try:
                with os.fdopen(fd, "w") as out:
                    json.dump(data, out, sort_keys=True)
                    out.write("\n")
                os.replace(temp, snapshot)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        return "Usage capture saved; existing baselines preserved. Capture before work, including resumed roles." + (
            " Partial coverage: " + "; ".join(data["errors"]) if data["errors"] else "")
    assert data is not None
    warnings += data["errors"]
    rows = {}
    for profile, ident in sorted(members):
        store = current.get(profile)
        session = store["sessions"].get(ident) if store else None
        if session is None:
            warnings.append(profile + ": session/store unavailable")
            continue
        captured_store = data["stores"].get(profile)
        baseline = captured_store["sessions"].get(ident) if captured_store else None
        if baseline is None:
            if session["parent"]:
                warnings.append(profile + ": inherited counters uncertain; missing baseline")
                continue
            if not captured_store or session["started"] < captured_store["captured"] or (profile, ident) not in fresh:
                warnings.append(profile + ": missing baseline")
                continue
            baseline = {"source": session["source"], "rows": {}, "captured": captured_store["captured"]}
        if baseline["captured"] > captured_store["captured"]:
            warnings.append("some session baselines start later; earlier usage excluded")
        if baseline["source"] != session["source"] or not set(baseline["rows"]) <= set(session["rows"]):
            warnings.append(profile + ": counter source/model changed or rows disappeared")
            continue
        if session["source"] == "session":
            warnings.append(profile + ": legacy session counters; model history and auxiliary usage unseparated")
        missing = set(session["missing"]) | set(baseline.get("missing", []))
        if missing:
            warnings.append(profile + ": missing counter fields " + ", ".join(sorted(missing)))
        deltas = {key: [n - old for n, old in zip(values, baseline["rows"].get(key, [0] * len(COUNTERS)))]
                  for key, values in session["rows"].items()}
        if any(n < 0 for values in deltas.values() for n in values):
            warnings.append(profile + ": counter reset")
            continue
        who = "this chat" if (profile, ident) in coordinators else "@" + profile
        for key, values in deltas.items():
            group = (who, *json.loads(key))
            counts = rows.setdefault(group, [0] * len(COUNTERS))
            for i, n in enumerate(values):
                counts[i] = None if COUNTERS[i] in missing or counts[i] is None else counts[i] + n
    if not members:
        warnings.append("no verified run identities")
    start_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(data["captured"]))
    end_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines = [f"Usage since capture: {start_at} to {end_at} (excludes work before each session baseline).",
             "| Who | Model | Provider | Task | Non-cached input | Cache reads | Cache writes | Output | Calls |",
             "|---|---|---|---|---|---|---|---|---|"]
    for labels, values in sorted(rows.items()):
        cells = [str("?" if cell is None else cell).replace("|", "\\|").replace("\n", " ")
                 for cell in (*labels, *values)]
        lines.append("| " + " | ".join(cells) + " |")
    if rows:
        total = ["?" if any(r[i] is None for r in rows.values()) else sum(r[i] for r in rows.values())
                 for i in range(len(COUNTERS))]
        lines.append("| **Total** | | | | " + " | ".join(map(str, total)) + " |")
    lines.append("Coverage: " + ("partial; " + "; ".join(sorted(set(warnings))) if warnings else
                 "measured deltas for recorded run identities; main and auxiliary task rows separate") +
                 ". Not a provider bill or quota debit.")
    return "\n".join(lines)


def _legacy_cost(run_dir, worktree):
    st = Path(run_dir).stat()
    since = getattr(st, "st_birthtime", st.st_ctime)
    tree, sid = str(Path(worktree).resolve()), os.environ.get("HERMES_SESSION_ID", "")
    rows = {}
    root = hermes_root()
    for store in [root / "state.db", *sorted(root.glob("profiles/*/state.db"))]:
        try:
            db = sqlite3.connect(f"file:{store}?mode=ro", uri=True, timeout=1)
            try:
                found = db.execute(
                    "SELECT id, profile_name, model, input_tokens, output_tokens, cache_read_tokens, cwd, started_at "
                    "FROM sessions WHERE (cwd = ? OR cwd LIKE ?) AND started_at >= ? OR id = ?",
                    (tree, tree + "/%", since, sid)).fetchall()
            finally:
                db.close()
        except sqlite3.Error:
            continue
        for ident, profile, model, inp, out, cached, *_ in found:
            who = "this chat" if ident == sid else "@" + (profile or store.parent.name)
            r = rows.setdefault((who, model or "?"), [0, 0, 0])
            for i, n in enumerate((inp, out, cached)):
                r[i] += n or 0
    if not rows:
        return "no sessions found: leave the tokens out"
    total = [sum(r[i] for r in rows.values()) for i in range(3)]

    def row(who, model, r):  # Hermes keeps uncached input and cache reads apart: the prompt is both
        prompt = r[0] + r[2]
        return f"| {who} | {model} | {_tokens(prompt + r[1])} | {r[2] * 100 // prompt if prompt else 0}% |"
    return "\n".join(["| Who | Model | Tokens | Cached |", "|---|---|---|---|",
                      *(row(w, m, r) for (w, m), r in sorted(rows.items(), key=lambda kv: -sum(kv[1]))),
                      row("**Total**", "", total)])


def active(run_dir, done=False):
    """Register this chat's run so the pinned rules find it after a compaction (__init__.py, _pinned)."""
    sid = os.environ.get("HERMES_SESSION_ID", "")
    if not sid:
        raise SystemExit("no HERMES_SESSION_ID in this shell: the pinned rules come from CPIPE_RUN instead")
    path = hermes_root() / "cpipe" / "active" / f"{sid}.json"
    if done:
        # The plugin finds a run through the chats this one was compacted from, so each of their records goes.
        for each in _lineage(sid):
            for suffix in (".json", ".watch"):
                path.with_name(each + suffix).unlink(missing_ok=True)
        return f"run closed for {sid}"
    if not (Path(run_dir) / "now.md").is_file():  # a flag or a wrong path would leave the chat without its run
        raise SystemExit(f"no now.md in {run_dir}: give the run dir (setup.md), nothing was registered")
    path.parent.mkdir(parents=True, exist_ok=True)
    run = str(Path(run_dir).resolve())
    path.write_text(json.dumps({"run_dir": run}) + "\n")
    # Every chat that ran it, kept after --done: a run continued in a new chat checks the user's words of all of them.
    chats = Path(run_dir) / "chats"
    if sid not in (chats.read_text().split() if chats.is_file() else []):
        with chats.open("a") as f:
            f.write(sid + "\n")
    out = f"run registered for {sid}"
    for lane in (hermes_root() / "cpipe" / "lanes").glob("*/*/run"):
        if lane.read_text().strip() not in (run_dir, run) or _alive(lane.parent):
            continue
        # Its launcher is gone and a chat picked the run up: the dispatcher leaves the lane to it (lanes()).
        (lane.parent / "taken").write_text(sid + "\n")
        out += f"\nlane {lane.parent}: taken over by this chat; the dispatcher posts this run's report.md"
    if not os.environ.get("CPIPE_LANE") and (line := _project(run_dir, sid)):
        out += "\n" + line
    return out


def _within(path, folder):
    return bool(path and folder) and (path == folder or path.startswith(folder.rstrip("/") + "/"))


def _project(run_dir, sid):
    """A `project:` line when this chat sits in none of the user's projects and one holds the run's repository
    (setup.md, This chat's project); a lane is filed by its launcher. Read-only, silent on anything missing."""
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    try:
        common = subprocess.run(["git", "-C", str(run_dir), "rev-parse", "--path-format=absolute",
                                 "--git-common-dir"], capture_output=True, text=True, timeout=10).stdout.strip()
        repo = str(Path(common).parent) if common else ""
        db = sqlite3.connect(f"file:{home / 'projects.db'}?mode=ro", uri=True, timeout=1)
        try:
            folders = db.execute("SELECT p.name, f.path FROM projects p JOIN project_folders f ON f.project_id = p.id"
                                 " WHERE p.archived = 0 UNION SELECT name, primary_path FROM projects"
                                 " WHERE archived = 0 AND primary_path IS NOT NULL").fetchall()
        finally:
            db.close()
        db = sqlite3.connect(f"file:{home / 'state.db'}?mode=ro", uri=True, timeout=1)
        try:
            row = db.execute("SELECT cwd, git_repo_root FROM sessions WHERE id = ?", (sid,)).fetchone() or ("", "")
        finally:
            db.close()
    except (OSError, sqlite3.Error, subprocess.SubprocessError):
        return ""
    if any(_within(p, f) for p in row if p for _, f in folders):
        return ""  # already in a project, whichever it is
    holds = sorted((len(f), name) for name, f in folders if _within(repo, f))
    return (f"project: this chat is in no project, and project '{holds[-1][1]}' holds {repo}: move this chat into it"
            " with your tool for the user's projects (steps/setup.md, This chat's project)") if holds else ""


def _fold(text):
    return " ".join(str(text).split())


TOLD = re.compile(r'^- (?:\[.?\]|met:|not yet:)\s*(.*)$')  # `- [x] words`, or `- met: "words"` as SKILL.md words it


def _told(run_dir):
    """The lines under now.md's Instructions: the user's instructions, word for word, without cpipe's own run
    text that /cpipe puts before the task (copied there, it is a line only the report could ever meet)."""
    try:
        told = re.search(r"(?ms)^## Instructions\n(.*?)(?=^## |\Z)", (Path(run_dir) / "now.md").read_text())
    except OSError:
        return []
    lines = []
    for line in told.group(1).splitlines() if told else []:
        if m := TOLD.match(line.strip()):
            words = m[1].strip()
            if len(words) > 1 and words[0] == words[-1] == '"':
                words = words[1:-1].replace('\\"', '"')
            if words and not _launch_text(words):
                lines.append(words)
    return lines


def _launch_text(line):
    """True for a line of cpipe's own run text (__init__.py START, SPEC_START, CONTINUE), not the user's."""
    global _LAUNCH
    if _LAUNCH is None:
        try:
            spec = importlib.util.spec_from_file_location("cpipe_plugin", Path(__file__).resolve().parents[3] / "__init__.py")
            plugin = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(plugin)
            _LAUNCH = _fold(" ".join((plugin.START, plugin.SPEC_START, plugin.CONTINUE)))
        except (OSError, AttributeError, SyntaxError):
            _LAUNCH = ""
    return len(_fold(line)) > 20 and _fold(line) in _LAUNCH


_LAUNCH = None


def unsaid(run_dir, sid=None):
    """Lines under now.md's Instructions the user never wrote in a chat of this run: a reading passed off as their
    words."""
    told = _told(run_dir)
    said = _said(sid or os.environ.get("HERMES_SESSION_ID", ""), run_dir) if told else None
    return [line for line in told if _fold(line) not in said] if said is not None else []


def unsourced(run_dir, sid=None):
    """R and C lines of spec.md whose `from: "…"` is missing or not the user's words: a limit the run made up."""
    spec = {k: v for k, v in _spec(run_dir).items() if k[0] in "RC"}
    if not spec:
        return []
    said = _said(sid or os.environ.get("HERMES_SESSION_ID", ""), run_dir)
    out = []
    for ident, line in spec.items():
        quote = re.search(r'from:\s*"((?:[^"\\]|\\.)+)"', line)  # the user's own quotes come escaped: \"
        if not quote or (said is not None and _fold(quote.group(1).replace('\\"', '"')) not in said):
            out.append(ident)
    return out


def _chats(run_dir):
    """The chats that ran this run: its `chats` record (`active`) and the chats registered on it now."""
    try:
        ids = (Path(run_dir) / "chats").read_text().split()
    except OSError:
        ids = []
    run = str(Path(run_dir).resolve())
    for rec in (hermes_root() / "cpipe" / "active").glob("*.json"):
        try:
            if json.loads(rec.read_text()).get("run_dir") == run and rec.stem not in ids:
                ids.append(rec.stem)
        except (OSError, ValueError):
            pass
    return ids


def _said(sid, run_dir=None):
    """Everything the user wrote in this chat, in every other chat of this run (a run continued in a new chat), and
    the chats each was compacted from, whitespace folded; None outside a Hermes chat (nothing to check against)."""
    db_path = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "state.db"
    if not (sid and db_path.is_file()):
        return None
    said, seen = [], set()
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for chat in [sid, *(_chats(run_dir) if run_dir else [])]:
            for each in _lineage(chat, db):
                if each in seen:
                    continue
                seen.add(each)
                said += [str(c) for (c,) in db.execute(
                    "SELECT content FROM messages WHERE session_id = ? AND role = 'user' AND content IS NOT NULL",
                    (each,))]
    finally:
        db.close()
    return _fold(" ".join(said))


def _lineage(sid, db=None):
    """This chat and the chats it was compacted from (a compaction continues the chat under a new id)."""
    ids = []
    try:
        own = db is None
        if own:
            path = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "state.db"
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            while sid and sid not in ids:
                ids.append(sid)
                row = db.execute("SELECT parent_session_id FROM sessions WHERE id = ?", (sid,)).fetchone()
                sid = row and row[0]
        finally:
            if own:
                db.close()
    except sqlite3.Error:
        pass
    return ids or [sid]


UNTRACKED = "instruction not on the checklist: {} (track add instruction <its words from now.md>, then close it)"
UNSAID = "instruction not in the user's words: {} (write only what they wrote; your reading goes in the run log)"


def chat():
    """The `hermes chat` command that runs a step on this chat's own profile, model and provider."""
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
    flags = ["hermes", f"-p {profile}"] if profile else ["hermes"]
    flags += ["chat"] + ([f"-m {model}"] if model else [])
    flags += [f"--provider {provider}"] if provider else []
    return " ".join(flags)


def brief(run_dir, worktree, role, round_, resume=False, card=False, bare=False):
    """A role's query: header.md's lines, filled in, then <run dir>/brief-<role>-<round>.md as the run wrote it.
    bare: the role has no profile of the user's, so it gets cpipe's stance; a profile works by its own setup."""
    skill = Path(__file__).resolve().parent.parent
    parts = dict(re.findall(r"(?ms)^## (\w+)\n(.*?)(?=^## |\Z)", (skill / "header.md").read_text()))
    try:
        told = re.search(r"(?ms)^## Instructions\n(.*?)(?=^## |\Z)", (Path(run_dir) / "now.md").read_text())
    except OSError:
        told = None
    if bad := unsaid(run_dir):  # a role takes these as settled, so a reading here decides the work
        raise SystemExit(" | ".join(UNSAID.format(line) for line in bad))
    stance = parts["Floor"] + parts[role] if bare and role in parts else ""
    head = parts["Lean"] if resume else parts["Card" if card else "Direct"] + stance + parts["Rules"] + parts["Lean"]
    for key, value in (("<worktree>", worktree), ("<run dir>", run_dir), ("<role>", role),
                       ("<skill dir>", str(skill)), ("<instructions>", told and told.group(1).strip() or "none")):
        head = head.replace(key, value)
    path = Path(run_dir) / f"brief-{role}-{round_}.md"
    if not path.exists():  # a run that started before briefs had their round in the name
        path = path.with_name(f"brief-{role}.md")
    return head + "\n" + path.read_text()

ROUND_CAPS = {"build": 3, "review": 2, "security": 3}  # SKILL.md, Rounds: build since the last plan or review, the rest per run


def rounds(run_dir):
    """Build rounds since the latest plan round and review, security and QA rounds over the whole run (a re-plan
    never buys another review), from logs/query-<role>-<n>.md in launch order."""
    runs = []
    for q in (Path(run_dir) / "logs").glob("query-*-*.md"):
        if m := re.fullmatch(r"query-(\w+)-(\d+)\.md", q.name):
            runs.append((q.stat().st_mtime, int(m[2]), m[1]))
    runs.sort()
    plans = [i for i, (_, _, role) in enumerate(runs) if role == "plan"]
    since = runs[plans[-1] + 1:] if plans else runs
    count = {role: sum(r == role for *_, r in (since if role == "build" else runs))
             for role in ("build", "review", "security", "qa")}
    builds = 0
    for *_, role in since:
        builds = 0 if role == "review" else builds + (role == "build")
    head = f"plan {runs[plans[-1]][1]}" if plans else "the start"
    line = (f"Rounds since {head}: build {count['build']} ({builds} since the last plan or review). "
            f"In the whole run: review {count['review']}, security {count['security']}, qa {count['qa']}.")
    full = [f"{role} at its cap of {cap}" for role, cap in ROUND_CAPS.items()
            if (builds if role == "build" else count[role]) >= cap]
    return line + (" At the cap (SKILL.md, Rounds): " + "; ".join(full) + "." if full else "")


REPORT_LINE = re.compile(r"^(?:✅ Done|⚠️? Done except|✅ Approved|⚠️? Changes needed)", re.M)
QUICK = 300  # a lane that stopped this soon after its start waits before it is resumed


def _segment(lane):
    """The lane's last session in log.txt: (its text, seconds it ran or None while running, exit code)."""
    try:
        log = (lane / "log.txt").read_text(errors="replace")
    except OSError:
        return None, None, None
    seg = log[log.rfind("lane started "):]
    start = re.match(r"lane started (\d+)", seg)
    end = re.search(r"^exit (\d+) (\d+)\s*\Z", seg, re.M)
    if not end:
        return seg, None, None
    ran = int(end.group(2)) - int(start.group(1)) if start else 0
    return seg, ran, int(end.group(1))


def _lane_report(lane, seg):
    """The lane's run's report.md (check_report), else the report in its log."""
    try:
        return (Path((lane / "run").read_text().strip()) / "report.md").read_text().strip()
    except OSError:
        return _report(seg)


def _report(seg):
    """The lane's final reply from its first report line, without the session footer."""
    found = REPORT_LINE.search(seg or "")
    if not found:
        return ""
    return re.split(r"^(?:session_id: |exit \d+ )", seg[found.start():], flags=re.M)[0].strip()


def _alive(lane):
    try:
        os.kill(int((lane / "pid").read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def _now_step(lane):
    """The first line of the `## Step` section of the lane's now.md (the run dir is in <lane>/run)."""
    try:
        text = (Path((lane / "run").read_text().strip()) / "now.md").read_text()
    except OSError:
        return "setting up"
    found = re.search(r"(?m)^## Step\w*\n+(.+)", text)
    return found.group(1).strip() if found else "running"


def _asking(lane, sid):
    """The lane's open questions when its gate says ASK: it waits for the user, never resumed around them."""
    try:
        verdict = gate((lane / "run").read_text().strip(), sid)
    except (OSError, SystemExit, KeyError, ValueError):
        return ""
    return verdict[5:] if verdict.startswith("ASK: ") else ""


def lanes(batch, at_once=4, report_of=None):
    """Each lane of a --parallel batch with its state, then what to do: START, RESUME, WAIT or DONE."""
    batch = Path(batch)
    rows, todo, running, queued = [], [], 0, []
    for lane in sorted((p for p in batch.iterdir() if (p / "brief.md").exists()), key=lambda p: (len(p.name), p.name)):
        issue = (lane / "brief.md").read_text().splitlines()[0].lstrip("# ").strip()
        seg, ran, code = _segment(lane)
        sid = re.findall(r"^session_id: (\S+)", seg or "", re.M)
        if seg is None:
            state = "queued"
            queued.append(lane.name)
        elif ran is None and _alive(lane):
            state = f"running · {_now_step(lane)}"
            running += 1
        elif _lane_report(lane, seg):
            state = "reported · " + _lane_report(lane, seg).splitlines()[0]
        elif sid and _asking(lane, sid[-1]):
            state = "❓ waiting on your answer in its chat · " + _asking(lane, sid[-1])
        elif (lane / "taken").exists():
            state = f"taken over in its chat · {_now_step(lane)}"
            running += 1
        else:
            how = f"exit {code} after {ran // 60} min" if ran is not None else "its process is gone"
            state = f"stopped · {how}" + (f" · session {sid[-1]}" if sid else " · never started")
            wait = f" -w {QUICK * 2}" if ran is not None and ran < QUICK else ""
            todo.append(f"RESUME {lane.name}{wait}" + (f" --resume {sid[-1]}" if sid else ""))
        rows.append(f"{lane.name} {issue} · {state}")
    free = max(at_once - running - len(todo), 0)
    todo += [f"START {name}" for name in queued[:free]]
    if not todo:
        todo = ["WAIT"] if running or queued else ["DONE"]
    out = rows + todo
    if report_of:
        report = _lane_report(batch / report_of, _segment(batch / report_of)[0])
        if report:
            out += ["", f"Report of {report_of}:", report]
    return "\n".join(out)


def _added(diff):
    """The added lines of a unified diff by file, whitespace folded."""
    out, f = {}, None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            f = line[6:] if line.startswith("+++ b/") else None
        elif f and line.startswith("+"):
            out.setdefault(f, []).append(" ".join(line[1:].split()))
    return out


def port(source, base, repo=None, root="."):
    """Source lines the port does not carry: each added line of the source change that no added line of this tree
    (its diff from the base, untracked files too) contains, the code's files before the tests'. Blank and
    punctuation-only lines are left out."""
    git = lambda d, *a: subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, check=True).stdout
    rng = source.split("..") if ".." in source else [source + "^1", source]
    src = _added(git(repo or root, "diff", *rng))
    here = " ".join(sum(_added(git(root, "diff", base)).values(), []))
    for f in git(root, "ls-files", "--others", "--exclude-standard").splitlines():
        here += " " + " ".join(Path(root, f).read_text(errors="replace").split())
    lines, missing = [], 0
    test = lambda f: bool(re.search(r"(^|/)(tests?|__tests__|spec)/|[._-](test|spec)\.", f))
    for f, added in sorted(src.items(), key=lambda i: (test(i[0]), i[0])):
        gone = [a for a in added if len(re.sub(r"\W", "", a)) >= 4 and a not in here]
        if gone:
            missing += len(gone)
            lines += [f"{f}: {len(gone)} source lines not in this change"] + [f"  + {a}" for a in gone]
    lines.append(f"FIX: {missing} source lines not in this change: port each, or give it its reason in the "
                 "inventory (ported in another form: where; not applicable: why)" if missing else "PASS")
    return "\n".join(lines)


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
    elif cmd == "ci":
        code, line = ci(args[0], args[1], int(dict(zip(args[2::2], args[3::2])).get("--every", 30)))
        print(line)
        raise SystemExit(code)
    elif cmd == "report":
        print(check_report(args[0]) if "--check" in args else report(args[0]))
    elif cmd == "active":
        print(active(args[0], "--done" in args))
    elif cmd == "chat":
        print(chat())
    elif cmd == "brief":
        print(brief(*args[:4], "--resume" in args, "--card" in args, "--bare" in args), end="")
    elif cmd == "rounds":
        print(rounds(args[0]))
    elif cmd == "cost":
        print(cost(args[0], args[1], "--start" in args))
    elif cmd == "step":
        print(_now_step(Path(args[0])))
    elif cmd == "port":
        opt = dict(zip(args[2::2], args[3::2]))
        print(port(args[0], args[1], opt.get("--repo")))
    elif cmd == "lanes":
        opt = dict(zip(args[1::2], args[2::2]))
        print(lanes(args[0], int(opt.get("--at-once", 4)), opt.get("--report")))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
