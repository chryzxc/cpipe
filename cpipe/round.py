"""``python -m cpipe.round <task> --result <file>``: one round of a delivery, launched by the runtime's ``round``
adapter in the card's worktree. Walks ``graph`` from where the delivery stands to an interrupt, then writes
``{status, summary, metadata}`` for the supervisor to apply (review / block / complete / requeue).

State lives in ``.cpipe/state.json`` in the worktree (git-excluded), so it survives across rounds; a copy goes
into the card's metadata at every interrupt. New input arrives as card comments: from anyone but cpipe it is
feedback for the engine; ``cpipe:approved`` and ``cpipe:ready`` (from the chat tools) are directives.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from . import engines, graph, pr, quality, settings, test_env, workspace_prep
from .home import root as hermes_home
from .runtime import hermes_api

ME = "cpipe"
DIRECTIVES = {"cpipe:approved": ("approved", True), "cpipe:ready": ("ready", True)}
CONTINUE = "Continue from the last commit and finish the task."
_spend_lock = threading.Lock()


def log(msg: str) -> None:
    print(f"[cpipe {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def state_path(ws: Path) -> Path:
    return ws / ".cpipe" / "state.json"


def load_state(ws: Path) -> Optional[dict]:
    try:
        return json.loads(state_path(ws).read_text())
    except (OSError, ValueError):
        return None


def save_state(ws: Path, state: dict) -> None:
    state_path(ws).parent.mkdir(exist_ok=True)
    state_path(ws).write_text(json.dumps(state, indent=1))


def new_state(ws: Path, task, engine: str) -> dict:
    project = workspace_prep.project_of(ws)
    profile = workspace_prep.project_profile(project)
    return {"v": 1, "project": project, "base": profile.get("base_branch") or workspace_prep.default_branch(ws), "engine": engine,
            "model": task.model_override, "node": "setup", "session": None, "last_comment_id": 0,
            "assumptions": [], "usage": {}, "by_node": {}, "timeline": [], "request": task.title}


def build_prompt(task, ws: Path, state: dict, notes: list[str]) -> str:
    profile = workspace_prep.project_profile(state["project"])
    conventions = "\n".join(str(profile.get("conventions") or "").strip().splitlines()[:10])
    lessons = workspace_prep.lessons(state["project"])
    return "\n\n".join(filter(None, [
        f"{task.title}\n\n{(task.body or '').strip()}".strip(),
        f"Worktree {ws}, branch {task.branch_name}, PR base {state['base']}. Project: {state['project']}.",
        "- Do the whole task in this session: read what you need, edit, add/adjust tests that prove it, run them, "
        "commit (Conventional Commits) on this branch. Keep the checklist in .cpipe/progress.md current.\n"
        "- Don't push, open PRs, merge, deploy, or touch production/credentials: the runner does that.\n"
        "- Don't stop to ask. Ambiguity → choose what a careful senior would and list it in `assumptions`. "
        "`needs_user` only for destructive, external, security or credential decisions.\n"
        "- Minimal change matching surrounding code; reuse existing helpers. Follow AGENTS.md.\n"
        "- Test first: a test that shows the change (or pins what must not break) comes before the code.\n"
        "- Independent pieces may go to subagents if your tool has them; you own and check their work.\n"
        "- No local paths or internal ids in code or commit messages.\n"
        "- `how_to_test`: the steps a reviewer follows to see the change work.",
        conventions,
        "Lessons from past deliveries:\n" + "\n".join(f"- {t}" for t in lessons) if lessons else "",
        "\n".join(f"Note: {n}" for n in notes),
    ]))


PLAN = ("Before building, plan, for the user to approve: don't change code yet. Read what you need in the repo. "
        "Write the plan to .cpipe/progress.md (what changes and where, the steps as a checklist, how it will be "
        "tested) and return it in `plan`, short and concrete. Put what only the user can decide in `questions`, "
        "each with your recommended answer; keep them few. If the task is really several independent changes "
        "that would each make a reviewable pull request on its own, list them in `parts` (title; request: a "
        "self-contained task description; after: the indexes of parts that must land first). Otherwise leave "
        "`parts` empty.")
APPROVED = "The user approved the plan in .cpipe/progress.md."
TESTS = ("Tests first, before any code change. If the change touches existing behavior, make sure tests pin what "
         "must not break: add them where missing; they must pass now. Then write the tests for the new or fixed "
         "behavior: they must fail now, because the code does not do it yet. Commit the tests only. Return `kind` "
         "(feature, fix, refactor: no behavior change, so no failing tests; none: nothing testable, e.g. docs), "
         "`guard_command` (runs the tests that must pass now, or empty), `red_command` (runs only the new tests "
         "that must fail now, or empty for refactor/none) and `tests` (the test names). The runner runs both "
         "commands to check.")
BUILD = ("Now implement the plan until the new tests and the whole suite pass (green). Don't weaken or delete the "
         "tests written first; if one is wrong, fix it and say why in `assumptions`. Then refactor what you "
         "touched while the tests stay green, and commit.")


def feedback(conn, task_id: str, state: dict) -> str:
    """New comments since the last read: directives update ``state``, the rest (from anyone but cpipe) is
    returned as feedback. Advances ``last_comment_id``."""
    new = hermes_api.comments_after(conn, task_id, state["last_comment_id"])
    if new:
        state["last_comment_id"] = new[-1].id
    said = []
    for c in new:
        if c.author in DIRECTIVES:
            key, value = DIRECTIVES[c.author]
            state[key] = value
        elif c.author != ME:
            said.append(f"{c.author}: {c.body.strip()}")
    return "\n\n".join(said)


def said_prompt(said: str) -> str:
    return f"New input from the user on this delivery:\n{said}\n\nAddress it, test, commit."


def spend(state: dict, node: str, result: engines.EngineResult) -> None:
    with _spend_lock:
        per = state.setdefault("by_node", {}).setdefault(node, {})
        for k, v in result.tokens.items():
            state["usage"][k] = state["usage"].get(k, 0) + v
            per[k] = per.get(k, 0) + v


def head(ws: Path) -> str:
    return subprocess.run(["git", "-C", str(ws), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def main_checkout(ws: Path) -> str:
    common = subprocess.run(["git", "-C", str(ws), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True).stdout.strip()
    return str(Path(common).parent)


@contextlib.contextmanager
def screen_lock():
    """Computer-use QA drives the one real screen: one at a time across all deliveries."""
    path = hermes_home() / "state" / "cpipe-computer-use.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


class Round:
    def __init__(self, task_id: str, ws: Path, conn, engine: str, cfg: dict):
        self.task_id, self.ws, self.conn, self.cfg = task_id, ws, conn, cfg
        self.task = hermes_api.get_task(conn, task_id)
        self.state = load_state(ws) or new_state(ws, self.task, engine)
        self.limits, self.rounds = cfg["limits"], {}
        self.fell_back = False
        self.last: Optional[engines.EngineResult] = None

    # --- engines ---

    def engine(self, name: Optional[str] = None) -> tuple[str, object, dict]:
        name = name or self.state["engine"]
        ec = dict(self.cfg["engines"].get(name) or {})
        if name == self.state["engine"] and self.state.get("model"):
            ec["model"] = self.state["model"]
        return name, engines.get(name, ec), ec

    def turn(self, node: str, prompt: str, schema: dict = engines.RESULT_SCHEMA, reasoning: Optional[str] = None,
             fallback: bool = True, profile: Optional[str] = None) -> engines.EngineResult:
        """One turn of the delivery's build session; at a usage limit, once per round, the fallback engine takes
        over from the commits."""
        name, eng, ec = self.engine()
        r = eng.run(ec, prompt, self.ws, session=self.state["session"], schema=schema, log=log,
                    reasoning=reasoning, profile=profile)
        spend(self.state, node, r)
        fb = self.cfg.get("fallback_engine")
        if (r.status == "rate_limited" and fallback and not self.fell_back and fb and fb != name
                and fb in self.cfg["engines"] and engines.available(fb, self.cfg["engines"][fb])[0]):
            self.fell_back = True
            log(f"{name} hit its usage limit: {fb} takes over")
            had_session = bool(self.state["session"])
            self.state.update(engine=fb, model=None, session=None)
            if had_session:
                prompt = (build_prompt(self.task, self.ws, self.state, []) + "\n\nAnother engine started this "
                          f"delivery and hit its usage limit. Its work is committed: see `git log "
                          f"origin/{self.state['base']}..HEAD` and .cpipe/progress.md. Continue from there.\n\n"
                          + prompt)
            return self.turn(node, prompt, schema, reasoning, fallback, None)
        if r.session:
            self.state["session"] = r.session
        return r

    # --- nodes: each returns (outcome, the next build prompt or "") ---

    def setup(self, prompt: str, said: str):
        notes = workspace_prep.prepare(self.ws, self.state["base"])
        prompt = build_prompt(self.task, self.ws, self.state, notes) + (f"\n\nAlso from the user:\n{said}" if said else "")
        return ("approved" if self.state.get("approved") else "ok"), prompt

    def plan(self, prompt: str, said: str):
        _, _, ec = self.engine()
        r = self.last = self.turn("plan", f"{prompt}\n\n{PLAN}", engines.PLAN_SCHEMA,
                                  reasoning=ec.get("plan_reasoning", "high"))
        if r.status in graph.ENGINE_DOWN or r.status == "needs_user":
            return r.status, prompt
        self.state.update(plan=r.data.get("plan") or r.summary, questions=r.data.get("questions") or [],
                          parts=r.data.get("parts") or [])
        return "ok", ""

    def split(self, prompt: str, said: str):
        ids: list[str] = []
        for i, part in enumerate(self.state["parts"]):
            after = [ids[j] for j in part.get("after") or [] if isinstance(j, int) and 0 <= j < len(ids)]
            ids.append(hermes_api.create_card(
                self.conn, title=part["title"], body=part["request"], assignee=self.task.assignee,
                repo=main_checkout(self.ws), branch=f"{self.task.branch_name}-{i + 1}",
                model=self.task.model_override, parents=after, creator_task_id=self.task_id))
            hermes_api.comment(self.conn, ids[-1], "part of an approved plan", author="cpipe:approved")
        self.state["children"] = ids
        return "ok", ""

    def tests(self, prompt: str, said: str):
        """Tests first, checked by running them: guard tests pass, the new tests fail (red), before the change."""
        r = self.last = self.turn("tests", f"{prompt}\n\n{TESTS}", engines.TESTS_SCHEMA)
        if r.status in graph.ENGINE_DOWN or r.status == "needs_user":
            return r.status, prompt
        d = r.data
        tdd = self.state["tdd"] = {"kind": d.get("kind") or "none", "tests": d.get("tests") or [],
                                   "red": (d.get("red_command") or "").strip(), "head": head(self.ws)}
        problems = []
        guard = (d.get("guard_command") or "").strip()
        if guard:
            g = quality._run(guard, self.ws)
            if g is None or g.returncode != 0:
                problems.append(f"The tests that pin current behavior fail before any change (`{guard}`):\n"
                                f"{(g.stdout if g else 'timed out')[-2000:]}")
        if tdd["kind"] in ("feature", "fix") and not tdd["red"]:
            problems.append("A feature or fix needs new tests that fail now: give their command in `red_command`.")
        elif tdd["red"]:
            red = quality._run(tdd["red"], self.ws)
            if red is not None and red.returncode == 0:
                problems.append(f"The new tests already pass (`{tdd['red']}`), so they don't test the change yet. "
                                "Write tests that fail until the change is made.")
        if problems:
            return graph.loop_outcome(["tests"], self.rounds, self.limits), "\n\n".join(problems) + "\n\nFix the tests and commit."
        return "ok", BUILD + (f" The new tests: `{tdd['red']}`." if tdd["red"] else "")

    def build(self, prompt: str, said: str):
        r = self.last = self.turn("build", prompt)
        self.state["assumptions"] += r.data.get("assumptions") or []
        for k in ("summary", "how_to_test"):
            if r.data.get(k):
                self.state[k] = r.data[k]
        return ("ok" if r.status == "done" else r.status), prompt

    def base_sha(self) -> str:
        return quality.diff_base(self.ws, self.state["base"]) or pr.base_ref(self.ws, self.state["base"])

    def checks(self, prompt: str, said: str):
        failures = quality.run_checks(self.ws, self.base_sha(), self.state["project"], card=self.task_id)
        out = graph.loop_outcome(["checks"] if failures else [], self.rounds, self.limits)
        if failures:
            return out, "The checks failed on your branch:\n\n" + "\n\n".join(failures) + "\n\nFix it and commit."
        return out, prompt

    def qa(self) -> tuple[Optional[bool], str]:
        mode = workspace_prep.project_profile(self.state["project"]).get("qa") or "browser"
        if mode == "off":
            return None, "QA: off for this project"
        app = test_env.start(str(self.ws), self.state["project"])
        if not app["ok"]:
            if "recipe" in app["error"] or "wtg" in app["error"]:
                return None, f"QA: skipped ({app['error']})"
            tails = "\n".join(f"{n}:\n{t}" for n, t in (app.get("log_tail") or {}).items())
            return False, f"The app did not start for QA: {app['error']}\n{app.get('output') or tails}".strip()
        how = "computer use (the real screen)" if mode == "computer-use" else "the browser"
        prompt = (f"QA: verify this delivery in the running app ({', '.join(app['urls'].values())}) using {how}, "
                  "the way a user would: the changed behavior and what is next to it. Save screenshots to "
                  ".cpipe/qa/. Don't edit code. Report pass, or fail with each problem.")
        _, _, ec = self.engine()
        with screen_lock() if mode == "computer-use" else contextlib.nullcontext():
            r = self.turn("qa", prompt, engines.QA_SCHEMA, fallback=False, profile=ec.get("qa_profile"))
        if r.status == "pass":
            return True, f"QA ({mode}): passed. {r.summary}".strip()
        if r.status == "fail":
            return False, "QA found problems in the running app:\n" + "\n".join(f"- {p}" for p in r.data.get("problems") or [r.summary])
        return None, f"QA: skipped (engine {r.status})"

    def tests_changed(self) -> list[str]:
        """Files the tests-first commit wrote that the build changed afterwards."""
        tdd = self.state.get("tdd") or {}
        if not tdd.get("head"):
            return []
        names = lambda *a: set(pr._git(self.ws, "diff", "--name-only", *a).stdout.split())
        return sorted(names(f"{self.base_sha()}..{tdd['head']}") & names(f"{tdd['head']}..HEAD"))

    def review(self, name: Optional[str] = None) -> tuple[Optional[bool], str]:
        name = name or self.cfg.get("review_engine")
        if not (name in self.cfg["engines"] and engines.available(name, self.cfg["engines"][name])[0]):
            name = self.state["engine"]
        eng_name, eng, ec = self.engine(name)
        diff = f"`git diff {self.base_sha()}...HEAD`"
        changed = self.tests_changed()
        if changed:
            diff += (f". Tests were written first and then changed during the build: {', '.join(changed)}. "
                     "Check each change is justified, not a weakened test")
        if self.state.get("review_session") and self.state.get("review_engine") == name:
            session, prompt = self.state["review_session"], f"The branch changed since your last review. Review it again: {diff}."
        else:
            session = None
            prompt = (f"You review a change before a human sees it. The request:\n\n{self.task.title}\n\n"
                      f"{(self.task.body or '').strip()}\n\nReview the branch: {diff} in {self.ws}. Don't edit files. "
                      "Report only what must change before merge: bugs, the request not met, missing or wrong tests, "
                      "security problems, clear breaks of the repo's conventions, and refactors that clearly pay "
                      "off in the changed code (duplication, needless complexity, unclear names). Approve when "
                      "nothing blocks; style preferences are not findings.")
        r = eng.run(ec, prompt, self.ws, session=session, schema=engines.REVIEW_SCHEMA, log=log, readonly=True)
        spend(self.state, "review", r)
        if r.status in graph.ENGINE_DOWN and name != self.state["engine"]:
            log(f"review on {name}: {r.status}; the delivery's engine reviews instead")
            return self.review(self.state["engine"])
        self.state.update(review_session=r.session or session, review_engine=name)
        if r.status == "approve":
            return True, f"Review ({eng_name}): approved"
        if r.status == "changes":
            self.state["findings"] = r.data.get("findings") or [r.summary]
            return False, "The independent review asks for changes:\n" + "\n".join(f"- {f}" for f in self.state["findings"])
        return None, f"Review: skipped ({eng_name} {r.status})"

    def security(self) -> tuple[Optional[bool], str]:
        failures = quality.run_checks(self.ws, self.base_sha(), self.state["project"], card=self.task_id,
                                      stage="security")
        return (False, "The security checks failed:\n\n" + "\n\n".join(failures)) if failures else (True, "Security checks: passed")

    def verify(self, prompt: str, said: str):
        """QA, security checks and the independent review, in parallel."""
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = {"qa": pool.submit(self.qa), "checks": pool.submit(self.security), "review": pool.submit(self.review)}
            done = {k: f.result() for k, f in jobs.items()}
        failed = [k for k, (ok, _) in done.items() if ok is False]
        out = graph.loop_outcome(failed, self.rounds, self.limits)
        tdd = self.state.get("tdd") or {}
        first = ([f"Tests first ({tdd['kind']}): {', '.join(tdd['tests'][:5]) or 'none'}"
                  + (", failing before the change, passing after" if tdd.get("red") else "")] if tdd else [])
        self.state["verification"] = first + ["Checks: passed"] + [text.splitlines()[0] for ok, text in done.values()]
        if out == "ok":
            self.state["verified_head"] = head(self.ws)
            if "review" in failed:  # out of review budget: the open findings go into the PR
                self.state["verification"] += [f"Open review finding: {f}" for f in self.state.get("findings", [])]
            return out, prompt
        return out, "\n\n".join(done[k][1] for k in failed) + "\n\nFix it, test, commit."

    def pr(self, prompt: str, said: str):
        out, detail = pr.publish(self.ws, self.state, self.task.branch_name)
        if out == "fix":
            return graph.loop_outcome(["checks"], self.rounds, self.limits), detail
        return out, detail if out == "blocked" else prompt

    def ci(self, prompt: str, said: str):
        out, detail = pr.ci(self.ws, self.state, settings.PR_MINUTES)
        self.state["ci"] = detail
        if out == "fail":
            return graph.loop_outcome(["ci"], self.rounds, self.limits), detail + "\n\nFix it and commit."
        return "ok", prompt

    def final(self, prompt: str, said: str):
        if head(self.ws) != self.state.get("verified_head"):
            failures = (quality.run_checks(self.ws, self.base_sha(), self.state["project"], card=self.task_id)
                        + quality.run_checks(self.ws, self.base_sha(), self.state["project"], card=self.task_id,
                                             stage="security"))
            if failures:
                return (graph.loop_outcome(["checks"], self.rounds, self.limits),
                        "The final checks failed:\n\n" + "\n\n".join(failures) + "\n\nFix it and commit.")
        err = pr.ready(self.ws, self.state)
        return ("blocked", f"Marking the pull request ready failed: {err}") if err else ("ok", "")

    # --- the round ---

    def start(self, said: str) -> tuple[str, str]:
        st, node = self.state, self.state["node"]
        if node not in graph.TERMINAL:  # a new delivery, or a round that died mid-way
            prompt = st.get("prompt") or ""
            return node, prompt + (f"\n\n{said_prompt(said)}" if said and node != "setup" else "")
        if node == "proposal" or (node in ("ask", "paused", *graph.ENGINE_DOWN) and not st.get("approved")):
            if st.get("approved"):
                also = f"\n\nAlso from the user:\n{said}" if said else ""
                return ("split" if len(st.get("parts") or []) >= 2 else "tests"), APPROVED + also
            return "plan", (f"The user's answers and changes to the plan:\n{said}\n\nRevise the plan."
                            if said else CONTINUE)
        if said:
            return "build", said_prompt(said)
        if st.get("ready"):
            return "final", ""
        if node in graph.ENGINE_DOWN or node == "paused":
            return st.get("at") or "build", st.get("prompt") or CONTINUE
        return "build", CONTINUE

    def run(self) -> dict:
        st, t0 = self.state, time.time()
        said = feedback(self.conn, self.task_id, st)
        st["node"], prompt = self.start(said)
        while st["node"] not in graph.TERMINAL:
            node, start = st["node"], time.time()
            if node in graph.ENGINE_NODES and time.time() - t0 > self.limits["round_minutes"] * 60:
                st.update(at=node, prompt=prompt, node="paused")
                break
            log(f"node {node} ({st['engine']})")
            outcome, prompt = getattr(self, node)(prompt, said)
            said = ""
            st["timeline"].append({"node": node, "start": int(start), "end": int(time.time()), "outcome": outcome,
                                   "engine": st["engine"]})
            if outcome == "limit" or outcome == "blocked":
                st["why"] = prompt
            to = graph.next_node(node, outcome, bool(st.get("ready")))
            if to in graph.ENGINE_DOWN:
                st["at"] = node
            if to in ("handoff", "final"):  # input that arrived during the round comes first
                late = feedback(self.conn, self.task_id, st)
                if late:
                    to, prompt = "build", said_prompt(late)
                elif st.get("ready"):
                    to = "final"
            st.update(node=to, prompt=prompt)
            save_state(self.ws, st)
        save_state(self.ws, st)
        return {"status": graph.TERMINAL[st["node"]], "summary": self.summary(), "metadata": {"cpipe": st}}

    def summary(self) -> str:
        st, end, r = self.state, self.state["node"], self.last
        if end == "handoff":
            where = st.get("pr_url") or (st.get("notes") or {}).get("pr") or ""
            return "\n\n".join(filter(None, [st.get("summary") or "Built, checked and reviewed.", where,
                                             "\n".join(st.get("verification") or [])]))
        if end == "proposal":
            parts = [f"{i + 1}. {p['title']}" for i, p in enumerate(st.get("parts") or [])]
            return "\n\n".join(filter(None, [
                "Plan, for your approval:\n\n" + (st.get("plan") or ""),
                "Questions (each with a recommended answer):\n" + "\n".join(f"- {q}" for q in st["questions"])
                if st.get("questions") else "",
                "Split into separate pull requests:\n" + "\n".join(parts) if len(parts) >= 2 else "",
                f"Answer or change it with /cp-feedback {self.task_id} <text>; approve with /cp-build {self.task_id}."]))
        if end == "ask":
            return (r.data.get("question") if r else "") or "The engine needs a decision."
        if end == "stuck":
            return st.get("why", "")[:4000] or "A loop reached its limit."
        if end == "paused":
            return (f"Paused after {self.limits['round_minutes']} minutes (at {st['at']}). Unblock the card to "
                    "continue from the last commit.")
        if end == "done":
            if st.get("children"):
                return f"Split into {len(st['children'])} deliveries: {', '.join(st['children'])}."
            return f"Accepted. The pull request is ready to merge: {st['pr_url']}" if st.get("pr_url") else "Accepted."
        return (r.summary if r else "") or end


def run_round(task_id: str, ws: Path, conn, engine: str, cfg: dict) -> dict:
    return Round(task_id, ws, conn, engine, cfg).run()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="cpipe.round")
    ap.add_argument("task_id")
    ap.add_argument("--result", required=True)
    args = ap.parse_args(argv)
    ws = Path(os.environ.get("WR_WORKSPACE") or os.getcwd())
    conn = hermes_api.connect(os.environ.get("WR_BOARD") or None)
    cfg = settings.load()
    engine = os.environ.get("WR_LANE") or cfg["default_engine"]
    out = run_round(args.task_id, ws, conn, engine, cfg)
    log(f"round ends: {out['status']}: {out['summary'][:300]}")
    Path(args.result).write_text(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
