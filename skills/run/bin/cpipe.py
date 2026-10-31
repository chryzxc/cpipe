"""cpipe's facts: check results, comparisons, the step checklist and the run's verdict.

It reads files and prints data and verdicts. It never starts a model, picks a step or stops a run: the model runs
every step and acts on what this prints (skills/run/SKILL.md). Standard library only.

  python3 cpipe.py results <out.json> <junit|jest|tsc|eslint|lines> <file>... [--root <dir>]
  python3 cpipe.py compare <run dir> <check>
  python3 cpipe.py track <run dir> add <step> <text> | set <id> <open|done|skip|user> [<evidence>, for user `<kind>: …`, a question `answer: R2: …`] | open [<step>]
  python3 cpipe.py gate <run dir>
  python3 cpipe.py report <run dir> [--check]
  python3 cpipe.py active <run dir> [--done]
  python3 cpipe.py chat
  python3 cpipe.py brief <run dir> <worktree> <role> <round> [--resume] [--card]
  python3 cpipe.py lanes <batch dir> [--at-once <n>] [--report <lane>]
  python3 cpipe.py step <lane dir>
"""

import json
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

STATUSES = ("open", "done", "skip", "user")
# What only the user may do (SKILL.md, Unattended): how the code should behave, or which fix, is never one
USER_KINDS = ("credentials", "destructive", "external", "approval", "answer")
NOT_USER = ("{} is the loop's work, not the user's: `user` is only for " + ", ".join(USER_KINDS) +
            " (evidence `<kind>: …`); a choice between fixes or scope is decided by the run")


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
SPEC_LINE = re.compile(r"^\s*-\s*\**([RCDNI]\d+)\**\s*:(.*)$")
TRACED = "RCD"  # the lines the delivery proves: requirements, constraints and the agreed design
UNTRACED = "spec {} has no checklist item: `track add plan \"{}: <the line>\"`, closed with its proof"
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
    for name in sorted(_bad(head)):
        if name in base_bad:
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


def gate(run_dir, sid=None):
    """SPEC while the spec is drafted, then FIX before WAIT before ASK before NEEDS-USER: the loop's own work first. PASS only when nothing is left."""
    if _drafting(run_dir):  # --spec: the user and the planner are still working out what to build
        return "SPEC: the plan profile is working out the spec with the user (spec.md, --spec)"
    fix, wait, ask, user = [], [], [], []
    items = _items(run_dir)
    told = set(map(_fold, _told(run_dir)))
    if not items:  # a run from before track.json, or one that lost it: nothing is proven yet
        fix.append("no checklist: add each step's Done when, the user's instructions and the merge conditions "
                   "with track add, closing each with its evidence")
    for i in items:
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
    fix += [UNSOURCED.format(ident) for ident in unsourced(run_dir, sid)]
    fix += [UNTRACED.format(ident, ident) for ident in _spec(run_dir) if ident[0] in TRACED and not _traced(items, ident)]
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
        elif bucket in ("pending", "in_progress", "queued"):
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
              "Cost: <minutes> · <tokens> (<cached share>) · <who did each step, on which model>", "Details: <one line>"]
    return "\n".join(parts)


REPORT_PARTS = ("## Test it", "## Proof", "## Run graph", "Risk: ", "Cost: ", "Details: ")


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
    return f"PASS: post {path} word for word as the final reply"


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
    run = str(Path(run_dir).resolve())
    path.write_text(json.dumps({"run_dir": run}) + "\n")
    out = f"run registered for {sid}"
    for lane in (hermes_root() / "cpipe" / "lanes").glob("*/*/run"):
        if lane.read_text().strip() not in (run_dir, run) or _alive(lane.parent):
            continue
        # Its launcher is gone and a chat picked the run up: the dispatcher leaves the lane to it (lanes()).
        (lane.parent / "taken").write_text(sid + "\n")
        out += f"\nlane {lane.parent}: taken over by this chat; the dispatcher posts this run's report.md"
    return out


def _fold(text):
    return " ".join(str(text).split())


def _told(run_dir):
    """The lines under now.md's Instructions: the user's instructions, word for word."""
    try:
        told = re.search(r"(?ms)^## Instructions\n(.*?)(?=^## |\Z)", (Path(run_dir) / "now.md").read_text())
    except OSError:
        return []
    lines = [re.sub(r"^- \[.?\]\s*", "", line).strip() for line in told.group(1).splitlines()
             if line.startswith("- [")] if told else []
    return [line for line in lines if line]


def unsaid(run_dir, sid=None):
    """Lines under now.md's Instructions the user never wrote in this chat: a reading passed off as their words."""
    told = _told(run_dir)
    said = _said(sid or os.environ.get("HERMES_SESSION_ID", "")) if told else None
    return [line for line in told if _fold(line) not in said] if said is not None else []


def unsourced(run_dir, sid=None):
    """R and C lines of spec.md whose `from: "…"` is missing or not the user's words: a limit the run made up."""
    spec = {k: v for k, v in _spec(run_dir).items() if k[0] in "RC"}
    if not spec:
        return []
    said = _said(sid or os.environ.get("HERMES_SESSION_ID", ""))
    out = []
    for ident, line in spec.items():
        quote = re.search(r'from:\s*"([^"]+)"', line)
        if not quote or (said is not None and _fold(quote.group(1)) not in said):
            out.append(ident)
    return out


def _said(sid):
    """Everything the user wrote in this chat and the chats it was compacted from, whitespace folded; None
    outside a Hermes chat (nothing to check against)."""
    db_path = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "state.db"
    if not (sid and db_path.is_file()):
        return None
    said, seen = [], set()
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        while sid and sid not in seen:  # this chat and the chats it was compacted from
            seen.add(sid)
            said += [str(c) for (c,) in db.execute(
                "SELECT content FROM messages WHERE session_id = ? AND role = 'user' AND content IS NOT NULL", (sid,))]
            row = db.execute("SELECT parent_session_id FROM sessions WHERE id = ?", (sid,)).fetchone()
            sid = row and row[0]
    finally:
        db.close()
    return _fold(" ".join(said))


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


def brief(run_dir, worktree, role, round_, resume=False, card=False):
    """A role's query: header.md's lines, filled in, then <run dir>/brief-<role>-<round>.md as the run wrote it."""
    skill = Path(__file__).resolve().parent.parent
    parts = dict(re.findall(r"(?ms)^## (\w+)\n(.*?)(?=^## |\Z)", (skill / "header.md").read_text()))
    try:
        told = re.search(r"(?ms)^## Instructions\n(.*?)(?=^## |\Z)", (Path(run_dir) / "now.md").read_text())
    except OSError:
        told = None
    if bad := unsaid(run_dir):  # a role takes these as settled, so a reading here decides the work
        raise SystemExit(" | ".join(UNSAID.format(line) for line in bad))
    head = parts["Lean"] if resume else parts["Card" if card else "Direct"] + parts["Rules"] + parts["Lean"]
    for key, value in (("<worktree>", worktree), ("<run dir>", run_dir), ("<role>", role),
                       ("<skill dir>", str(skill)), ("<instructions>", told and told.group(1).strip() or "none")):
        head = head.replace(key, value)
    path = Path(run_dir) / f"brief-{role}-{round_}.md"
    if not path.exists():  # a run that started before briefs had their round in the name
        path = path.with_name(f"brief-{role}.md")
    return head + "\n" + path.read_text()


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
        print(check_report(args[0]) if "--check" in args else report(args[0]))
    elif cmd == "active":
        print(active(args[0], "--done" in args))
    elif cmd == "chat":
        print(chat())
    elif cmd == "brief":
        print(brief(*args[:4], "--resume" in args, "--card" in args), end="")
    elif cmd == "step":
        print(_now_step(Path(args[0])))
    elif cmd == "lanes":
        opt = dict(zip(args[1::2], args[2::2]))
        print(lanes(args[0], int(opt.get("--at-once", 4)), opt.get("--report")))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
