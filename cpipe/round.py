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
import hashlib
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from . import code_context, discovery, engines, graph, learning, pr, quality, settings, test_env, workspace_prep
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
    prompt = "\n\n".join(filter(None, [
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
        learning.context(ws) if state.get("size") != "content" else "",
        (discovery.context(state) or code_context.context(ws, task.title, state)) if state.get("size") != "content" else "",
        "\n".join(f"Note: {n}" for n in notes),
    ]))
    if state.get("size") == "content":
        prompt = prompt.replace("edit, add/adjust tests that prove it, run them, ", "edit only the requested content, ")
        prompt = prompt.replace("- Test first: a test that shows the change (or pins what must not break) comes before the code.",
                                "- Content-only: do not add tests or change application logic.")
    return prompt


PLAN = ("Before building, plan, for the user to approve: don't change code yet. Read what you need in the repo. "
        "Write the plan to .cpipe/progress.md (what changes and where, the steps as a checklist, how it will be "
        "tested) and return it in `plan`, short and concrete. Put what only the user can decide in `questions`, "
        "each with your recommended answer; keep them few. If the task is really several independent changes "
        "that would each make a reviewable pull request on its own, list them in `parts` (title; request: a "
        "self-contained task description; after: the indexes of parts that must land first). Otherwise leave "
        "`parts` empty. Name the domain data shape and organizing structure before logic. Include a short "
        "throughput checkpoint: blocking prerequisites, independent workstreams, shared mutable state, "
        "and the smallest safe decomposition. Mark non-applicable dimensions with a reason; one coupled "
        "change stays with one owner. Use runtime evidence for empirical decisions, not user guesswork.")
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

# Shared by review/verify/ship so a complete review can be reused at shipping.
# Version the contract in gate_key: older approvals must not bypass new requirements.
REVIEW_CONTRACT_VERSION = 1
COMPLETENESS_REVIEW = (
    "Completion review, not merely a diff/style check:\n"
    "- Trace the requested behavior through its real callers and dependencies. Check correctness, security, "
    "compatibility and adjacent regressions against the approved scope.\n"
    "- Inspect tests for the changed behavior, preserved behavior, relevant edge/error cases and integration "
    "boundaries. Missing, weak or incorrect coverage is a finding; passing configured checks alone does not "
    "prove adequate coverage. Request concrete tests, not arbitrary coverage percentages.\n"
    "- Assess behavior-preserving refactors in the changed code: remove harmful duplication or needless "
    "complexity when it materially improves correctness or maintainability. No cosmetic churn, unrelated "
    "rewrites or scope expansion. Refactor only when justified, not on every shipping run.\n"
    "- Require actual focused-test and applicable existing regression-suite evidence for testable changes. "
    "Inspect the commands, results and revision in the supplied check evidence and repository artifacts. "
    "If evidence is missing, request executable checks with the correction; unavailable or unrun checks "
    "are not passes. Explicitly justify genuinely untestable/content-only scope.\n"
    "- Return all actionable gaps in one verdict. The builder, not the reviewer, adds tests, fixes code and "
    "performs justified refactors; independently re-review corrections before publication."
)
CORRECTION_CONTRACT = (
    "For these bounded corrections: add missing regression/characterization tests before editing code. "
    "A test for an existing correct behavior may pass immediately; reproduce an actual bug with a failing "
    "test before fixing it. Do not claim retrospective TDD. Preserve behavior during justified refactors; "
    "do not weaken tests or expand scope. Run focused tests and the applicable existing regression suite, "
    "record exact commands/results in .cpipe/progress.md, and commit the corrected code and tests. "
    "If required executable checks are missing, configure them through the project's existing supported "
    "check configuration or report the missing dependency. Independent review and verification follow."
)


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
        elif c.author == "cpipe:size":
            if state.get("node") == "setup" and c.body.strip() in ("large", "small", "content"):
                state["size"] = c.body.strip()
        elif c.author in ("cpipe:adopt", "cpipe:stage"):
            directive = json.loads(c.body)
            if c.author == "cpipe:adopt" and state.get("node") == "setup":
                state.update(adoption=directive, adopted=True, base=directive["base"])
            state.update(entry=directive["entry"], publish=directive.get("publish", False),
                         ready=directive.get("ready", False), stage_requested=True,
                         stage_text=directive.get("text") or directive.get("request") or "")
            if directive.get("text"):
                state["review_scope"] = directive["text"]
        elif c.author != ME:
            said.append(f"{c.author}: {c.body.strip()}")
    if said:
        state["review_scope"] = "\n\n".join(filter(None, [state.get("review_scope"), *said]))
    return "\n\n".join(said)


def said_prompt(said: str) -> str:
    return f"New input from the user on this delivery:\n{said}\n\nAddress it, test, commit."


def spend(state: dict, node: str, result: engines.EngineResult) -> None:
    with _spend_lock:
        per = state.setdefault("by_node", {}).setdefault(node, {})
        for k, v in (result.tokens or {}).items():
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
        self.limits = cfg["limits"]
        self.rounds = self.state.setdefault("rounds", {})
        # Migrate older single-engine state once; newer state records each engine/profile owner.
        self.state.setdefault("session_owners", {})
        if "sessions" not in self.state:
            self.state["sessions"] = {}
            if self.state.get("session"):
                name, eng, ec = self.engine()
                self.state["sessions"][name] = self.state["session"]
                self.state["session_owners"][name] = [eng.__name__, ec.get("profile") or "default"]
        self.fell_back = False
        self.last: Optional[engines.EngineResult] = None

    # --- engines ---

    def engine(self, name: Optional[str] = None) -> tuple[str, object, dict]:
        name = name or self.state["engine"]
        ec = dict(self.cfg["engines"].get(name) or {})
        if name == self.state["engine"] and self.state.get("model"):
            ec["model"] = self.state["model"]
        return name, engines.get(name, ec), ec

    def role_engine(self, node: str) -> str:
        role = "build" if node == "tests" else node
        configured = self.state["engine"] if role == "build" else self.cfg.get("roles", {}).get(role)
        return self.state.get("role_overrides", {}).get(role) or configured or self.state["engine"]

    def call(self, node: str, name: str, eng, ec: dict, prompt: str, **kwargs) -> engines.EngineResult:
        start = time.monotonic()
        r = eng.run(ec, prompt, self.ws, log=log, **kwargs)
        spend(self.state, node, r)
        with _spend_lock:
            self.state.setdefault("agent_calls", []).append({
                "node": node, "engine": name, "profile": kwargs.get("profile") or ec.get("profile"),
                "seconds": round(time.monotonic() - start, 3), "status": r.status, "tokens": r.tokens})
        return r

    def discover(self) -> None:
        """One optional, isolated navigation call. Never block or fall back to a costly builder."""
        name = self.cfg.get("roles", {}).get("discover")
        if not name or self.state.get("size", "large") != "large":
            self.state.pop("discovery", None)
            return
        # Only Hermes has the tool-free snapshot mode. No session/model override inheritance.
        ec = dict(self.cfg["engines"][name])
        if ec.get("kind", name) != "hermes":
            self.state["discovery"] = {"status": "unavailable", "detail": "Discovery requires a Hermes engine"}
            return
        ec["discovery_only"] = True
        key = None
        try:
            request = self.task.title + "\n" + (self.task.body or "") + "\n" + (self.state.get("review_scope") or "")
            key, prompt = discovery.prepare(self.ws, request, self.state, ec)
            if self.state.get("discovery", {}).get("key") == key:
                return
            self.state["discovery"] = {"key": key, "status": "unavailable"}
            if not engines.available(name, ec)[0]:
                return
            result = self.call("discover", name, engines.get(name, ec), ec, prompt,
                               session=None, schema=discovery.SCHEMA, readonly=True)
            brief = result.data.get("brief") if isinstance(result.data, dict) else None
            self.state["discovery"].update(status=result.status)
            if result.status == "done" and isinstance(brief, str) and brief.strip():
                self.state["discovery"]["brief"] = brief.strip()[:discovery.MAX_BRIEF]
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.state["discovery"] = {"key": key, "status": "unavailable", "detail": type(exc).__name__}

    def turn(self, node: str, prompt: str, schema: dict = engines.RESULT_SCHEMA, reasoning: Optional[str] = None,
             fallback: bool = True, profile: Optional[str] = None) -> engines.EngineResult:
        """One turn of the delivery's build session; at a usage limit, once per round, the fallback engine takes
        over from the commits."""
        name, eng, ec = self.engine(self.role_engine(node))
        # QA always owns a separate session, even if it uses the builder's CLI/profile.
        key = f"qa:{name}:{profile or ec.get('profile') or ''}" if node == "qa" else name
        owner = [eng.__name__, profile or ec.get("profile") or "default"]
        session = self.state["sessions"].get(key) if self.state["session_owners"].get(key) == owner else None
        if not session and node != "qa":
            self.discover()
            context = build_prompt(self.task, self.ws, self.state, [])
            if not prompt.startswith(context):
                prompt = (context + "\n\nApproved scope:\n"
                          + (self.state.get("plan") or self.task.body or self.task.title) + "\n\n" + prompt)
        r = self.call(node, name, eng, ec, prompt, session=session, schema=schema,
                      reasoning=reasoning, profile=profile)
        fb = self.cfg.get("fallback_engine")
        if (r.status == "rate_limited" and fallback and not self.fell_back and fb and fb != name
                and fb in self.cfg["engines"] and engines.available(fb, self.cfg["engines"][fb])[0]):
            self.fell_back = True
            log(f"{name} hit its usage limit: {fb} takes over")
            had_session = bool(session)
            role = "build" if node == "tests" else node
            self.state.setdefault("role_overrides", {})[role] = fb
            if role == "build" or not self.cfg.get("roles", {}).get(role):
                self.state.update(engine=fb, model=None, session=None)
            if had_session:
                prompt = (build_prompt(self.task, self.ws, self.state, []) + "\n\nAnother engine started this "
                          f"delivery and hit its usage limit. Its work is committed: see `git log "
                          f"origin/{self.state['base']}..HEAD` and .cpipe/progress.md. Continue from there.\n\n"
                          + prompt)
            return self.turn(node, prompt, schema, reasoning, fallback, None)
        if r.session:
            self.state["sessions"][key] = r.session
            self.state["session_owners"][key] = owner
            if node in ("tests", "build"):
                self.state["session"] = r.session
        return r

    # --- nodes: each returns (outcome, the next build prompt or "") ---

    def remember(self, patterns: list[str], *, verified: bool = False) -> None:
        if self.state.get("size") == "content":
            return
        pending = sorted(set(self.state.get("learning_patterns", [])) | set(patterns))
        self.state["learning_patterns"] = pending
        if pending:
            # Remote CI recovery needs a green final-head CI result, not a local verify receipt.
            signals = [p for p in pending if not verified or p != "ci"]
            warning = learning.record(self.ws, self.task_id, signals, verified=verified)
            if warning:
                self.state["learning_warning"] = warning

    def setup(self, prompt: str, said: str):
        # An adopted branch is frozen work, not a fresh delivery branch. Never
        # reset it to the base just because its commit already exists on a remote.
        adopted = self.state.get("adopted")
        if adopted and head(self.ws) != self.state.get("adoption", {}).get("head"):
            return "blocked", "The adopted commit changed before setup; adopt the current revision again."
        notes = workspace_prep.prepare(self.ws, None if adopted else self.state["base"])
        self.discover()
        prompt = build_prompt(self.task, self.ws, self.state, notes) + (f"\n\nAlso from the user:\n{said}" if said else "")
        if self.state.get("adopted"):
            if head(self.ws) != self.state["adoption"]["head"]:
                return "blocked", "The adopted worktree is not at the requested revision; nothing was reviewed."
            self.state["approved"] = True
            self.state["stage_requested"] = False
            self.state["verification"] = ["Adopted existing committed work; tests-first history was not asserted."]
            return self.state["entry"], self.stage_prompt(prompt)
        size = self.state.get("size", "large")
        if size in ("small", "content"):
            self.state["approved"] = True  # explicit fast-path submission authorizes implementation
        if size == "content":
            prompt += "\n\nContent-only scope. Do not add tests or change application logic."
            return "content", prompt
        return ("approved" if self.state.get("approved") else "ok"), prompt

    def stage_prompt(self, prompt: str = "") -> str:
        if self.state.get("entry") == "fix":
            findings = "; ".join(self.state.get("findings") or [])
            return (prompt + "\n\nFix only these bounded findings/improvements: "
                    + (self.state.get("stage_text") or findings)
                    + "\nAdd regression/characterization tests first; existing correct behavior may already pass."
                      " Reproduce actual bugs before fixing them. Do not claim the adopted work was TDD."
                      " Test, commit, and return for independent review of the new revision.")
        return prompt

    def review_stage(self, prompt: str, said: str):
        if not self.clean_revision():
            return "blocked", "Review needs a clean committed worktree; commit the application changes first."
        approved, detail = self.review()
        if approved is False:
            self.remember(["review"])
        self.state["stage_result"] = detail
        return ("blocked", detail) if approved is None else ("ok", prompt)

    def plan(self, prompt: str, said: str):
        _, _, ec = self.engine(self.role_engine("plan"))
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
            if red is None or red.returncode in (126, 127):
                return "blocked", "The tests-first command could not run; unavailable tests are not evidence of RED."
            if red.returncode == 0:
                problems.append(f"The new tests already pass (`{tdd['red']}`), so they don't test the change yet. "
                                "Write tests that fail until the change is made.")
        if problems:
            return graph.loop_outcome(["tests"], self.rounds, self.limits), "\n\n".join(problems) + "\n\nFix the tests and commit."
        return "ok", BUILD + (f" The new tests: `{tdd['red']}`." if tdd["red"] else "")

    def build(self, prompt: str, said: str):
        if self.state.get("size") != "content" and (self.state.get("entry") == "fix" or any(self.rounds.values())):
            prompt += "\n\n" + CORRECTION_CONTRACT
        r = self.last = self.turn("build", prompt)
        self.state["assumptions"] += r.data.get("assumptions") or []
        for k in ("summary", "how_to_test"):
            if r.data.get(k):
                self.state[k] = r.data[k]
        return ("ok" if r.status == "done" else r.status), prompt

    def clean_revision(self) -> bool:
        from .runtime.gitstate import porcelain
        paths = porcelain(self.ws)
        return paths is not None and not any(not p.startswith(".cpipe/") for p in paths)

    def gate_key(self) -> dict:
        """Only immutable revision + scope + gate configuration earns reuse, never agent claims."""
        profile = workspace_prep.project_profile(self.state["project"])
        stamps = {}
        for ec in self.cfg["engines"].values():
            if (ec.get("kind") or "") == "hermes" or ec.get("profile"):
                name = ec.get("profile") or "default"
                root = Path.home() / ".hermes"
                path = root / "config.yaml" if name == "default" else root / "profiles" / name / "config.yaml"
                try:
                    stamps[name] = path.stat().st_mtime_ns
                except OSError:
                    stamps[name] = None
        recipe = self.ws / "wtg.yaml"
        config = {"roles": self.cfg.get("roles"), "engines": self.cfg["engines"],
                  "review_engine": self.cfg.get("review_engine"), "profile": profile,
                  "profile_configs": stamps, "quality": quality.load(), "scope": [self.task.title, self.task.body],
                  "size": self.state.get("size"), "model": self.state.get("model"),
                  "review_scope": self.state.get("review_scope", ""),
                  "review_contract": REVIEW_CONTRACT_VERSION,
                  "recipe": recipe.read_text() if recipe.exists() else None}
        digest = hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()
        return {"head": head(self.ws), "base": self.base_sha(), "config": digest}

    def base_sha(self) -> str:
        return (self.state.get("adoption", {}).get("base_head")
                or quality.diff_base(self.ws, self.state["base"])
                or pr.base_ref(self.ws, self.state["base"]))

    def checks(self, prompt: str, said: str):
        if not self.clean_revision():
            return "blocked", "Checks need a clean committed application revision."
        key = self.gate_key()
        if self.state.get("checks_receipt") == key:
            return "ok", prompt
        evidence = []
        failures = quality.run_checks(self.ws, self.base_sha(), self.state["project"], card=self.task_id, evidence=evidence)
        self.state["checks_evidence"] = evidence
        if any(f.startswith("[unavailable]") for f in failures):
            return "blocked", "Required checks could not run:\n" + "\n".join(failures)
        if self.gate_key() != key or not self.clean_revision():
            return "blocked", "The revision or gate configuration changed during checks; rerun on a frozen revision."
        out = graph.loop_outcome(["checks"] if failures else [], self.rounds, self.limits)
        if not failures:
            self.state["checks_receipt"] = key
        if failures:
            return out, "The checks failed on your branch:\n\n" + "\n\n".join(failures) + "\n\nFix it and commit."
        return out, prompt

    def qa(self) -> tuple[Optional[bool], str]:
        if self.state.get("size") == "content":
            return None, "QA: not needed for content-only scope"
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
        if self.state.get("review_scope"):
            prompt += "\nAdditional acceptance scope:\n" + self.state["review_scope"]
        _, _, ec = self.engine(self.role_engine("qa"))
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
        name = name or self.cfg.get("roles", {}).get("review") or self.cfg.get("review_engine")
        if not (name in self.cfg["engines"] and engines.available(name, self.cfg["engines"][name])[0]):
            if self.cfg.get("roles", {}).get("review"):
                return None, f"Review: configured role {name} is unavailable"
            name = self.state["engine"]
        eng_name, eng, ec = self.engine(name)
        current_head = head(self.ws)
        key = self.gate_key()
        if self.state.get("review_receipt") == key and not self.state.get("findings") and self.clean_revision():
            return True, f"Review ({eng_name}): approved (same revision; reused)"
        owner = [eng_name, eng.__name__, ec.get("profile") or "default"]
        session = (self.state.get("review_session")
                   if self.state.get("review_owner") == owner and self.state.get("review_engine") == name else None)
        inputs = {k: v for k, v in key.items() if k != "head"}
        previous_head = self.state.get("review_head") if session else None
        if self.state.get("review_inputs") and self.state["review_inputs"] != inputs:
            previous_head = None  # changed scope/config/base must be reviewed across the full range
        if previous_head and subprocess.run(["git", "-C", str(self.ws), "merge-base", "--is-ancestor",
                                             previous_head, current_head], capture_output=True).returncode:
            previous_head = None  # a rebase/rewritten history needs full-range review
        diff = (f"`git diff {previous_head}..{current_head}`" if previous_head
                else f"`git diff {self.base_sha()}...{current_head}`")
        if previous_head:
            diff += ". Review this delta and verify every prior finding: " + "; ".join(self.state.get("findings") or [])
        changed = self.tests_changed()
        if changed:
            diff += (f". Tests were written first and then changed during the build: {', '.join(changed)}. "
                     "Check each change is justified, not a weakened test")
        if session:
            prompt = f"The branch changed since your last review. Review it again: {diff}."
        else:
            session = None
            prompt = (f"You review a change before a human sees it. The request:\n\n{self.task.title}\n\n"
                      f"{(self.task.body or '').strip()}\n\nReview the branch: {diff} in {self.ws}. Don't edit files. "
                      "Report only what must change before merge: bugs, the request not met, missing or wrong tests, "
                      "security problems, clear breaks of the repo's conventions, and refactors that clearly pay "
                      "off in the changed code (duplication, needless complexity, unclear names). Approve when "
                      "nothing blocks; style preferences are not findings.")
        if self.state.get("findings") and not previous_head:
            prompt += "\nVerify prior findings too: " + "; ".join(self.state["findings"])
        prompt += "\nReturn all findings in one verdict with stable IDs REV-001, REV-002, etc."
        if self.state.get("size") != "content":
            prompt += "\n\n" + code_context.context(self.ws, self.task.title if self.task else "existing change", self.state)
        if self.state.get("size") == "content":
            prompt += "\nContent-only scope. Do not require new tests or unrelated refactors. Check wording, layout and preserved behavior."
        else:
            prompt += "\n\n" + COMPLETENESS_REVIEW
            prompt += "\nCurrent revision check evidence (missing evidence is not a pass):\n" + json.dumps({
                "current_head": current_head,
                "checks_receipt": self.state.get("checks_receipt"),
                "checks": self.state.get("checks_evidence") or [],
            }, sort_keys=True)
        if self.state.get("review_scope"):
            prompt += "\nAdditional requested review scope: " + self.state["review_scope"]
        r = self.call("review", eng_name, eng, ec, prompt, session=session, schema=engines.REVIEW_SCHEMA, readonly=True)
        if self.gate_key() != key or not self.clean_revision():
            self.state.pop("review_receipt", None)
            return None, "Review: the frozen revision or gate configuration changed; restore it and rerun review"
        if r.status in graph.ENGINE_DOWN and name != self.state["engine"] and not self.cfg.get("roles", {}).get("review"):
            log(f"review on {name}: {r.status}; the delivery's engine reviews instead")
            return self.review(self.state["engine"])
        self.state.update(review_session=r.session or session, review_engine=name, review_owner=owner)
        if r.status in ("approve", "changes"):
            self.state.update(review_head=current_head, review_inputs=inputs)
        if r.status == "approve":
            if self.gate_key() != key:
                return None, "Review: revision or gate configuration changed during the verdict; rerun review"
            self.state.update(review_receipt=key, findings=[])
            return True, f"Review ({eng_name}): approved"
        if r.status == "changes":
            self.state["findings"] = r.data.get("findings") or [r.summary]
            return False, "The independent review asks for changes:\n" + "\n".join(f"- {f}" for f in self.state["findings"])
        return None, f"Review: skipped ({eng_name} {r.status})"

    def security(self) -> tuple[Optional[bool], str]:
        evidence = []
        failures = quality.run_checks(self.ws, self.base_sha(), self.state["project"], card=self.task_id,
                                      stage="security", evidence=evidence)
        self.state["security_evidence"] = evidence
        if any(f.startswith("[unavailable]") for f in failures):
            return None, "Required security checks could not run:\n" + "\n".join(failures)
        label = self.check_label("Security checks", evidence)
        return (False, "The security checks failed:\n\n" + "\n\n".join(failures)) if failures else (True, label)

    @staticmethod
    def check_label(label: str, evidence: list) -> str:
        if not evidence:
            return f"{label}: none configured (not a test pass)"
        statuses = [r["status"] for r in evidence]
        if "unavailable" in statuses:
            return f"{label}: optional checks unavailable; residual risk"
        return f"{label}: passed" if "pass" in statuses else f"{label}: not applicable to changed files"

    def verify(self, prompt: str, said: str):
        """Independent gates on one revision, in parallel; exact-revision receipts avoid repeat model calls."""
        key = self.gate_key()
        if self.state.get("verification_receipt") == key and self.clean_revision():
            self.state["verified_head"] = key["head"]
            self.remember([], verified=True)
            return "ok", prompt
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = {"qa": pool.submit(self.qa), "checks": pool.submit(self.security), "review": pool.submit(self.review)}
            done = {k: f.result() for k, f in jobs.items()}
        self.remember(["security" if k == "checks" else k for k, (ok, _) in done.items() if ok is False])
        if done["checks"][0] is None:
            return "blocked", done["checks"][1]
        if done["review"][0] is None:
            return "blocked", done["review"][1]  # an explicitly configured reviewer is a required gate
        if (done["qa"][0] is None
                and self.state.get("size") != "content"
                and workspace_prep.project_profile(self.state["project"]).get("qa") != "off"):
            return "blocked", done["qa"][1]
        failed = [k for k, (ok, _) in done.items() if ok is False]
        out = graph.loop_outcome(failed, self.rounds, self.limits)
        tdd = self.state.get("tdd") or {}
        first = (["Adopted existing work; tests-first history was not asserted."] if self.state.get("adopted") else ([f"Tests first ({tdd['kind']}): {', '.join(tdd['tests'][:5]) or 'none'}"
                  + (", failing before the change, passing after" if tdd.get("red") else "")] if tdd else []))
        self.state["verification"] = first + [self.check_label("Checks", self.state.get("checks_evidence") or [])] + [text.splitlines()[0] for ok, text in done.values()]
        if out == "ok":
            if self.gate_key() != key or not self.clean_revision():
                return "blocked", "The revision or gate configuration changed during verification; reverify the final revision."
            self.state.update(verified_head=key["head"], verification_receipt=key)
            self.remember([], verified=True)
            return out, prompt
        return out, "\n\n".join(done[k][1] for k in failed) + "\n\nFix it, test, commit."

    def pr(self, prompt: str, said: str):
        if (not self.clean_revision() or self.state.get("verification_receipt") != self.gate_key()
                or head(self.ws) != self.state.get("verified_head")):
            return "blocked", "Publishing requires successful review and verification of this exact clean revision."
        out, detail = pr.publish(self.ws, self.state, self.task.branch_name)
        if out == "fix":
            return graph.loop_outcome(["checks"], self.rounds, self.limits), detail
        return out, detail if out == "blocked" else prompt

    def ci(self, prompt: str, said: str):
        out, detail = pr.ci(self.ws, self.state, settings.PR_MINUTES)
        self.state["ci"] = detail
        if out == "fail":
            return graph.loop_outcome(["ci"], self.rounds, self.limits), detail + "\n\nFix it and commit."
        if out != "ok":
            return "blocked", detail
        self.state["ci_head"] = head(self.ws)
        if ("ci" in self.state.get("learning_patterns", [])
                and self.state.get("ci_check_evidence", {}).get("head") == self.state["ci_head"]
                and self.state.get("verification_receipt") == self.gate_key() and self.clean_revision()):
            warning = learning.record(self.ws, self.task_id, ["ci"], verified=True)
            if warning:
                self.state["learning_warning"] = warning
        return "ok", prompt

    def final(self, prompt: str, said: str):
        key = self.gate_key()
        if (not self.clean_revision() or self.state.get("checks_receipt") != key
                or self.state.get("verification_receipt") != key or self.state.get("verified_head") != key["head"]):
            return "reverify", "The revision or gate inputs changed; rerun checks and independent verification."
        if self.state.get("pr_url") and self.state.get("ci_head") != key["head"]:
            out, detail = self.ci(prompt, said)
            if out != "ok":
                return out, detail
        err = pr.ready(self.ws, self.state)
        return ("blocked", f"Marking the pull request ready failed: {err}") if err else ("ok", "")

    # --- the round ---

    def start(self, said: str) -> tuple[str, str]:
        st, node = self.state, self.state["node"]
        if st.pop("stage_requested", False) and node != "setup":
            entry = st["entry"]
            return {"review": "review_stage", "fix": "build", "verify": "checks", "ship": "checks"}[entry], self.stage_prompt()
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
            if node in ("tests", "checks", "ci") and outcome in ("fix", "limit"):
                self.remember([node])
            said = ""
            st["timeline"].append({"node": node, "start": int(start), "end": int(time.time()), "outcome": outcome,
                                   "engine": st["engine"]})
            if outcome == "limit" or outcome == "blocked":
                st["why"] = prompt
            to = graph.next_node(node, outcome, bool(st.get("ready")))
            if node == "verify" and outcome == "ok" and st.get("publish") is False:
                to = "verified"
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
        if end in ("reviewed", "verified"):
            if end == "reviewed":
                findings = st.get("findings") or []
                return "\n".join([st.get("stage_result") or "Reviewed existing work.",
                                  *findings, "No changes published. Use /cp-fix to correct findings or /cp-verify to run remaining gates."])
            return "\n".join(["Verified this revision locally; no changes published.",
                              *(st.get("verification") or []), "Use /cp-ship to prepare the draft pull request."])
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
                "Answer or request changes through Hermes native clarification; approve with /cp-build in this session."]))
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
