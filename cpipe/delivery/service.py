"""Pure, revision-fenced decisions; disconnected from live execution.

Adapters must authenticate events and derive receipts from observed executions.
Intent IDs are semantic deduplication keys, not leases or a tool/filesystem sandbox.
"""
from dataclasses import MISSING, dataclass, fields, replace
from .contracts import Versioned, Receipt, Outcome, identity, relative_path
from .schema import ExecutionPlan
from .findings import Finding, FindingRecord, blocking, blocks, merge_findings, stalled, disposition

PHASES = frozenset({"proposal", "checking", "implementing", "testable", "reviewing",
                    "locally_ready", "publishing", "completed", "waiting", "diagnosis",
                    "cancelled", "stopped"})
KINDS = frozenset({"proposal_created", "envelope_approved", "activity_claimed",
                   "activity_observed", "review_received", "feedback_received",
                   "clarification_answered", "publication_observed", "cancelled", "resumed",
                   "activity_abandoned", "activity_refused", "worktree_adopted",
                   "deadline_reached"})


# Attempts to get a behavior test that fails on an assertion before implementing.
CONTRACT_REASONS = frozenset({"criteria_not_in_output", "no_report", "no_test_cases"})
WRITE_TEST_ROUNDS = 3

def nonnegative(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid {name}")


def optional_id(value, name):
    if type(value) is not str:
        raise ValueError(f"invalid {name}")
    if value:
        identity(value, name)


def typed_tuple(value, cls, name):
    if type(value) is not tuple or any(type(v) is not cls for v in value):
        raise ValueError(f"invalid {name}")


@dataclass(frozen=True)
class State(Versioned):
    plan: ExecutionPlan
    artifact: str
    version: int
    phase: str
    activity: str
    active_intent: str
    run_id: str
    worker_session: str
    builder_session: str
    builder_digest: str
    receipts: tuple[Receipt, ...]
    findings: tuple[FindingRecord, ...]
    approved: bool
    readiness_requested: bool
    review_cycles: int
    last_reviewed_artifact: str
    calls_used: int
    started_at: int
    last_at: int
    # Takeover: the approved fallback actor now in charge ("" = the first), and the
    # test/review counts when the current builder took over, so it gets its own rounds.
    builder: str = ""
    reviewer: str = ""
    handover_tests: int = 0
    handover_reviews: int = 0
    renewals: int = 0  # times the user granted a fresh budget to continue
    stalls: int = 0  # review rounds in a row that did not reduce the blocking findings
    regressions_checked: str = ""  # the tree that passed the final regression review

    def __post_init__(self):
        if type(self.plan) is not ExecutionPlan or self.phase not in PHASES:
            raise ValueError("invalid snapshot plan or phase")
        identity(self.artifact, "artifact")
        for name in ("version", "review_cycles", "calls_used", "started_at", "last_at",
                     "handover_tests", "handover_reviews", "renewals", "stalls"):
            nonnegative(getattr(self, name), name)
        if self.last_at < self.started_at:
            raise ValueError("time moved backwards")
        if type(self.approved) is not bool or type(self.readiness_requested) is not bool:
            raise ValueError("invalid snapshot flags")
        for name in ("activity", "active_intent", "run_id", "worker_session", "builder_session",
                     "builder_digest", "last_reviewed_artifact", "builder", "reviewer", "regressions_checked"):
            optional_id(getattr(self, name), name)
        typed_tuple(self.receipts, Receipt, "receipts")
        typed_tuple(self.findings, FindingRecord, "findings")
        if len({f.finding.id for f in self.findings}) != len(self.findings):
            raise ValueError("duplicate finding IDs")

    def to_dict(self):
        data = super().to_dict()
        data.update(plan=self.plan.to_dict(), receipts=[r.to_dict() for r in self.receipts],
                    findings=[f.to_dict() for f in self.findings])
        return data

    @classmethod
    def from_dict(cls, data):
        if type(data) is dict:  # snapshots saved before newer defaulted fields existed
            data = {**{f.name: f.default for f in fields(cls) if f.default is not MISSING}, **data}
        values = cls._payload(data)
        values["plan"] = ExecutionPlan.from_dict(values["plan"])
        for name in ("receipts", "findings"):
            if type(values[name]) is not list:
                raise ValueError(f"invalid {name}")
        values["receipts"] = tuple(Receipt.from_dict(r) for r in values["receipts"])
        values["findings"] = tuple(FindingRecord.from_dict(f) for f in values["findings"])
        return cls(**values)


@dataclass(frozen=True)
class Verification(Versioned):
    finding_id: str
    receipt: Receipt
    evidence_ref: str

    def __post_init__(self):
        identity(self.finding_id, "finding ID")
        relative_path(self.evidence_ref)
        if type(self.receipt) is not Receipt:
            raise ValueError("invalid verification receipt")

    def to_dict(self):
        data = super().to_dict()
        data["receipt"] = self.receipt.to_dict()
        return data

    @classmethod
    def from_dict(cls, data):
        values = cls._payload(data)
        values["receipt"] = Receipt.from_dict(values["receipt"])
        return cls(**values)


@dataclass(frozen=True)
class Event(Versioned):
    key: str
    kind: str
    delivery_id: str
    plan_digest: str
    artifact: str
    expected_version: int
    now: int
    session_id: str = ""
    profile_id: str = ""
    activity_id: str = ""
    run_id: str = ""
    actor_id: str = ""
    worker_session: str = ""
    result_artifact: str = ""
    action: str = ""
    receipts: tuple[Receipt, ...] = ()
    findings: tuple[Finding, ...] = ()
    verifications: tuple[Verification, ...] = ()
    verdict: str = ""
    reviewed_from: str = ""
    reviewed_to: str = ""
    evidence_ref: str = ""
    plan: ExecutionPlan | None = None
    target: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("key", "delivery_id", "plan_digest", "artifact", "session_id", "profile_id"):
            identity(getattr(self, name), name)
        if self.kind not in KINDS:
            raise ValueError("unsupported event")
        nonnegative(self.expected_version, "expected version")
        nonnegative(self.now, "event time")
        for name in ("activity_id", "run_id", "actor_id", "worker_session", "result_artifact", "action",
                     "verdict", "reviewed_from", "reviewed_to", "evidence_ref"):
            optional_id(getattr(self, name), name)
        typed_tuple(self.receipts, Receipt, "receipts")
        typed_tuple(self.findings, Finding, "findings")
        typed_tuple(self.verifications, Verification, "verifications")
        typed_tuple(self.target, str, "target")
        for item in self.target:
            identity(item, "target")
        if self.plan is not None and type(self.plan) is not ExecutionPlan:
            raise ValueError("invalid proposed plan")

    def to_dict(self):
        data = super().to_dict()
        data.update(receipts=[r.to_dict() for r in self.receipts], findings=[f.to_dict() for f in self.findings],
                    verifications=[v.to_dict() for v in self.verifications],
                    plan=self.plan.to_dict() if self.plan else None)
        return data

    @classmethod
    def from_dict(cls, data):
        values = cls._payload(data)
        for name, klass in (("receipts", Receipt), ("findings", Finding), ("verifications", Verification)):
            if type(values[name]) is not list:
                raise ValueError(f"invalid {name}")
            values[name] = tuple(klass.from_dict(v) for v in values[name])
        if type(values["target"]) is not list:
            raise ValueError("invalid target")
        values["target"] = tuple(values["target"])
        values["plan"] = ExecutionPlan.from_dict(values["plan"]) if values["plan"] is not None else None
        return cls(**values)


def current(state, receipt):
    return receipt.observed and matches(state, receipt)


def matches(state, receipt):
    """The receipt is for this artifact and frozen check, whatever its outcome."""
    check = next((c for c in state.plan.checks if c.obligation == receipt.obligation), None)
    return bool(check and receipt.artifact == state.artifact
                and receipt.scope == state.plan.envelope.scope
                and (receipt.inputs, receipt.environment, receipt.command)
                == (check.inputs, check.environment, check.command))


def green(state, include_review=True):
    required = set(state.plan.envelope.required)
    if not include_review:
        required.discard("review")
    latest = {r.obligation: r for r in state.receipts}
    return bool(required) and all(name in latest and current(state, latest[name])
                                  and latest[name].outcome is Outcome.PASS for name in required)


def final_pass(state):
    """The normal review approved this exact tree; one fresh regression review is still owed."""
    review = next((r for r in reversed(state.receipts) if r.obligation == "review"), None)
    return bool(review and current(state, review) and review.outcome is Outcome.PASS
                and state.regressions_checked != state.artifact)


def verified(state):
    """Checks green, reviewed, and the final regression review passed on this exact tree."""
    return green(state) and state.regressions_checked == state.artifact


def actor(state, role):
    """The approved actor in charge of role: the first, until it hands over."""
    current = getattr(state, role)
    return next(a for a in state.plan.actors if a.role == role and (not current or a.profile == current))


def takeover(state, role):
    """State with the next approved actor of role in charge, or None when none is left."""
    names = [a.profile for a in state.plan.actors if a.role == role]
    later = names[names.index(actor(state, role).profile) + 1:]
    if not later:
        return None
    if role == "reviewer":
        return replace(state, reviewer=later[0])
    # A fresh session with its own test and review rounds.
    return replace(state, builder=later[0], builder_session="", builder_digest="", stalls=0,
                   handover_tests=sum(r.obligation == "behavior" for r in state.receipts),
                   handover_reviews=state.review_cycles)


def resumable(state):
    """Stopped, waiting, or handed over with review findings left unresolved: "continue" picks it up."""
    return (state.phase in {"waiting", "diagnosis", "stopped"}
            or state.phase == "locally_ready" and blocking(r.finding for r in state.findings))


def deadline(state):
    """When the approved time runs out. A renewal restarts the budget past the plan's own expiry."""
    end = state.started_at + state.plan.budget.seconds
    return end if state.renewals else min(state.plan.expires_at, end)


def resume_point(state):
    """Where a resumed run picks up: re-observe the code, or prove the failing test first."""
    if final_pass(state):
        return continue_with(state, "review", "reviewing")
    if state.plan.completion_target == "draft" and verified(state):
        return continue_with(state, "publish", "publishing")  # verified and reviewed: only the PR is left
    proved = any(r.obligation == "behavior" and r.outcome is Outcome.FAIL for r in state.receipts)
    return continue_with(state, "checks" if proved else "tests", "checking")


def continue_with(state, action, phase, feedback_ref=""):
    calls = state.calls_used + int(action in {"write_tests", "implement", "review"})
    if calls > state.plan.budget.calls:
        return replace(state, phase="stopped", activity="", active_intent="", run_id=""), ()
    role = "reviewer" if action == "review" else "builder"
    worker = actor(state, role)
    key = f"{state.plan.envelope.delivery_id}:{state.version}:{action}"
    resume = state.builder_session if role == "builder" and state.builder_digest == state.plan.digest() else ""
    intent = {"schema": 1, "delivery": state.plan.envelope.delivery_id,
              "action": action, "artifact": state.artifact, "plan_digest": state.plan.digest(),
              "role": role, "actor": worker.to_dict(), "resume": resume,
              "review_from": state.last_reviewed_artifact or state.plan.base_commit,
              "requires_full_coverage": True}
    if feedback_ref:
        intent["feedback_ref"] = feedback_ref
    return replace(state, activity=action, phase=phase, active_intent=key, run_id="",
                   worker_session="", calls_used=calls), ((key, intent),)


def quiescent(state, phase):
    return replace(state, phase=phase, activity="", active_intent="", run_id="", worker_session=""), ()


def check_result_identity(state, event):
    if (not state.approved or not state.run_id or event.run_id != state.run_id
            or event.activity_id != state.active_intent or event.worker_session != state.worker_session):
        raise ValueError("result does not match claimed activity")
    if state.activity in {"write_tests", "implement", "review"}:
        role = "reviewer" if state.activity == "review" else "builder"
        if event.actor_id != actor(state, role).profile:
            raise ValueError("result actor mismatch")


def accept_review(state, event):
    if state.activity != "review" or not event.verdict or not event.evidence_ref:
        raise ValueError("review requires current verdict and evidence")
    relative_path(event.evidence_ref)
    final = final_pass(state)
    if (event.reviewed_from != state.plan.base_commit or event.reviewed_to != state.artifact
            or len(event.receipts) != 1 or event.receipts[0].obligation != "review"
            or not current(state, event.receipts[0])):
        raise ValueError("review lacks current full-range coverage")
    if any(f.disposition != "open" for f in event.findings):
        raise ValueError("incoming finding disposition must be open")
    if len({v.finding_id for v in event.verifications}) != len(event.verifications):
        raise ValueError("duplicate prior-ID verification")
    prior = {r.finding.id: r for r in state.findings}
    before = {key for key, r in prior.items() if blocks(r.finding)}
    signatures = {(f.criterion, f.root_cause) for f in event.findings}
    for v in event.verifications:
        if v.finding_id not in prior or not current(state, v.receipt):
            raise ValueError("unknown finding or stale verification")
        old = prior[v.finding_id]
        if v.receipt.outcome is Outcome.PASS:
            if (old.finding.criterion, old.finding.root_cause) in signatures:
                raise ValueError("contradictory recurrence and verification")
            target = "verified"
        elif (v.receipt.outcome is Outcome.FAIL
              and (old.finding.criterion, old.finding.root_cause) in signatures):
            target = "open"
        else:
            raise ValueError("verification lacks disposition proof")
        if old.finding.disposition != target:
            prior[v.finding_id] = disposition(
                old, target, artifact=state.artifact, scope=state.plan.envelope.scope,
                verdict=event.verdict, reviewed_range=f"{event.reviewed_from}..{event.reviewed_to}",
                evidence_ref=v.evidence_ref, receipt=v.receipt, policy_authorized=True)
    merged = merge_findings([r.finding for r in prior.values()], event.findings)
    for f in merged:
        if f.disposition != "open" and (f.criterion, f.root_cause) in signatures:
            raise ValueError("recurrence requires current failure proof")
    records = tuple(prior.get(f.id) or FindingRecord(f) for f in merged)
    state = replace(state, findings=records, receipts=state.receipts + event.receipts,
                    review_cycles=state.review_cycles + 1, last_reviewed_artifact=state.artifact)
    after = {r.finding.id for r in records if blocks(r.finding)}
    cycles = state.review_cycles - state.handover_reviews
    state = replace(state, stalls=state.stalls + 1 if after and cycles >= 2 and len(after) >= len(before) else 0)
    if after:
        if stalled(cycles, before, after, state.stalls):
            fresh = takeover(state, "builder")  # a stuck fix loop gets fresh eyes before the user
            if fresh:
                return continue_with(fresh, "implement", "implementing", event.evidence_ref)
            if green(state, include_review=False):
                # More fix rounds would only trade findings: hand over working code with the
                # unresolved findings listed for the user, rather than stopping empty-handed.
                return quiescent(state, "locally_ready")
            return continue_with(state, "diagnose", "diagnosis", event.evidence_ref)
        return continue_with(state, "implement", "implementing", event.evidence_ref)
    if not green(state):
        return continue_with(state, "diagnose", "waiting", event.evidence_ref)
    if not final:  # nothing ships on the normal review alone
        return continue_with(state, "review", "reviewing")
    state = replace(state, regressions_checked=state.artifact)
    if state.plan.completion_target == "draft":
        return continue_with(state, "publish", "publishing")
    return quiescent(state, "locally_ready")


def apply(snapshot, event):
    """Accept a bound fact, version its projection and emit at most one continuation."""
    if type(event) is not Event:
        raise ValueError("invalid event")
    if snapshot is None:
        p = event.plan
        if (event.kind != "proposal_created" or p is None or event.expected_version != 0
                or event.plan_digest != p.digest() or event.delivery_id != p.envelope.delivery_id
                or event.session_id != p.envelope.session_id or event.profile_id != p.envelope.profile_id):
            raise ValueError("first event must be a bound proposal")
        if event.now >= p.expires_at:
            raise ValueError("proposal expired")
        state = State(p, event.artifact, 1, "proposal", "", "", "", "", "", "", (), (),
                      False, False, 0, "", 0, event.now, event.now)
        return continue_with(state, "approve_envelope", "proposal")
    if type(snapshot) is not State:
        raise ValueError("invalid snapshot")
    e = snapshot.plan.envelope
    if (event.delivery_id != e.delivery_id or event.plan_digest != snapshot.plan.digest()
            or event.artifact != snapshot.artifact or event.expected_version != snapshot.version
            or event.session_id != e.session_id or event.profile_id != e.profile_id
            or event.now < snapshot.last_at):
        raise ValueError("stale or unbound event")
    if snapshot.phase in {"completed", "cancelled"} or snapshot.phase == "stopped" and event.kind != "resumed":
        raise ValueError("terminal delivery cannot continue")
    state = replace(snapshot, version=snapshot.version + 1, last_at=event.now)
    if event.kind == "cancelled":
        return quiescent(state, "cancelled")
    if event.kind == "resumed":
        # "Continue" after a stop: every approved builder and reviewer gets its full rounds again,
        # from the code as it is now (result_artifact). Out of time or calls, the same plan gets a
        # fresh budget of the approved size: asking to continue is the consent for it.
        if (not state.approved or not resumable(state)
                or state.activity not in {"", "diagnose", "clarify"}):
            raise ValueError("only an approved delivery that stopped or waits can be resumed")
        state = replace(state, artifact=event.result_artifact or state.artifact, builder="", reviewer="",
                        builder_session="", builder_digest="", stalls=0,
                        handover_tests=sum(r.obligation == "behavior" for r in state.receipts),
                        handover_reviews=state.review_cycles)
        if event.now >= deadline(state) or state.calls_used >= state.plan.budget.calls:
            state = replace(state, started_at=event.now, calls_used=0, renewals=state.renewals + 1)
        return resume_point(state)
    if event.now >= deadline(state):
        return quiescent(state, "stopped")
    if event.kind == "deadline_reached":
        raise ValueError("the budget has not run out")
    if event.kind == "envelope_approved":
        if state.phase != "proposal" or event.activity_id != state.active_intent:
            raise ValueError("approval does not match pending proposal")
        # The execution budget starts at consent, not while the plan awaits it.
        return resume_point(replace(state, approved=True, started_at=event.now))
    if event.kind == "activity_claimed":
        if (state.activity not in {"tests", "write_tests", "implement", "checks", "review", "publish"}
                or event.activity_id != state.active_intent or state.run_id or not event.run_id):
            raise ValueError("claim does not match pending activity")
        builder, digest = state.builder_session, state.builder_digest
        if state.activity in {"write_tests", "implement", "review"}:
            role = "reviewer" if state.activity == "review" else "builder"
            if event.actor_id != actor(state, role).profile or not event.worker_session:
                raise ValueError("claim actor/session mismatch")
            if role == "reviewer" and event.worker_session == state.builder_session:
                raise ValueError("reviewer cannot inherit builder session")
            if role == "builder":
                if (state.builder_session and state.builder_digest == state.plan.digest()
                        and event.worker_session != state.builder_session):
                    raise ValueError("builder claim must resume the bound session")
                builder, digest = event.worker_session, state.plan.digest()
        return replace(state, run_id=event.run_id, worker_session=event.worker_session,
                       builder_session=builder, builder_digest=digest), ()
    if event.kind == "worktree_adopted":
        # The worktree drifted and could not be restored, but stays inside the approved scope:
        # continue from it and prove it again with the checks before any review.
        if (not state.approved or state.run_id or not event.result_artifact
                or state.activity not in {"write_tests", "tests", "implement", "checks", "review"}):
            raise ValueError("only an unclaimed approved step can adopt the worktree")
        state = replace(state, artifact=event.result_artifact)
        if state.activity == "review":
            return continue_with(state, "checks", "checking")
        return continue_with(state, state.activity, state.phase)
    if event.kind == "activity_abandoned":
        if not state.run_id or event.run_id != state.run_id or event.activity_id != state.active_intent:
            raise ValueError("abandon does not match claimed activity")
        # A crashed step is re-issued on a fresh card. It spends no call: the attempt produced nothing,
        # and the controller's strike limit bounds the retries.
        if event.action == "fresh":  # the bound session itself may be the problem
            state = replace(state, builder_session="", builder_digest="")
        refund = int(state.activity in {"write_tests", "implement", "review"})
        return continue_with(replace(state, calls_used=state.calls_used - refund), state.activity, state.phase)
    if event.kind == "activity_refused":
        if not state.run_id or event.run_id != state.run_id or event.activity_id != state.active_intent:
            raise ValueError("refusal does not match claimed activity")
        if state.activity == "publish" or event.action == "environment":
            # Push or PR failed, or a tool or dependency is missing: the work waits for "continue".
            return quiescent(state, "waiting")
        if state.activity not in {"write_tests", "implement", "review"}:
            raise ValueError("only worker steps can be refused")
        # The next approved actor takes over and the flow continues; none left: ask the user.
        fresh = takeover(state, "reviewer" if state.activity == "review" else "builder")
        return continue_with(fresh, state.activity, state.phase) if fresh else quiescent(state, "waiting")
    if event.kind in {"activity_observed", "review_received", "publication_observed"}:
        check_result_identity(state, event)
    if event.kind == "activity_observed":
        if state.activity in {"write_tests", "implement"}:
            if not event.result_artifact:
                raise ValueError("implementation needs an observed successor artifact")
            if (state.activity == "implement" and event.result_artifact == state.artifact
                    and green(state, include_review=False)):
                # It disputed the findings and changed nothing: the passing checks still hold.
                return continue_with(state, "review", "reviewing")
            after = "tests" if state.activity == "write_tests" else "checks"
            return continue_with(replace(state, artifact=event.result_artifact), after, "checking")
        if state.activity not in {"tests", "checks"} or not event.receipts:
            raise ValueError("check activity requires command observations")
        # UNAVAILABLE receipts are unobserved by design (e.g. the test does not exist yet);
        # a PASS/FAIL must have been observed.
        if any(not matches(state, r) or r.obligation == "review"
               or (r.outcome is not Outcome.UNAVAILABLE and not r.observed) for r in event.receipts):
            raise ValueError("stale or misconfigured check receipt")
        earlier = sum(r.obligation == "behavior" for r in state.receipts)
        last = {r.obligation: r.reason for r in state.receipts}
        state = replace(state, receipts=state.receipts + event.receipts)
        # The environment, or a frozen check command that cannot observe its criteria
        # however the code changes (twice in a row): only the user can fix either.
        if any(r.reason == "command_not_found" or r.reason in CONTRACT_REASONS and last.get(r.obligation) == r.reason
               for r in event.receipts):
            return continue_with(state, "diagnose", "waiting")
        if state.activity == "tests" and not any(r.outcome is Outcome.FAIL for r in event.receipts):
            # Only an observed RED proves the behavior test covers the change. A
            # missing, passing or broken test goes back to the builder with the reason.
            if earlier - state.handover_tests < WRITE_TEST_ROUNDS:
                return continue_with(state, "write_tests", "implementing")
            fresh = takeover(state, "builder")
            if fresh:  # the same number of write_tests rounds as the first builder
                return continue_with(replace(fresh, handover_tests=earlier), "write_tests", "implementing")
            return continue_with(state, "diagnose", "diagnosis")
        if any(r.outcome in {Outcome.FAIL, Outcome.UNAVAILABLE} for r in event.receipts):
            # Crashes, timeouts and build errors are code problems the builder can repair.
            return continue_with(state, "implement", "implementing")
        if not green(state, include_review=False):
            return continue_with(state, "diagnose", "waiting")
        if state.readiness_requested or not state.plan.interactive:
            return continue_with(state, "review", "reviewing")
        return quiescent(state, "testable")
    if event.kind == "review_received":
        return accept_review(state, event)
    if event.kind == "feedback_received":
        if state.phase not in {"testable", "locally_ready", "waiting", "diagnosis"}:
            raise ValueError("feedback requires a quiescent delivery")
        if event.plan is not None:
            old, new = state.plan, event.plan
            if (new.envelope.delivery_id, new.envelope.session_id, new.envelope.profile_id,
                    new.envelope.repo, new.worktree, new.common_git_dir) != (
                    old.envelope.delivery_id, old.envelope.session_id, old.envelope.profile_id,
                    old.envelope.repo, old.worktree, old.common_git_dir):
                raise ValueError("scope change cannot transfer delivery ownership")
            return continue_with(replace(state, plan=new, approved=False, receipts=(), readiness_requested=False),
                                 "approve_envelope", "proposal")
        if event.action == "ready":
            return continue_with(replace(state, readiness_requested=True),
                                 "review" if green(state, include_review=False) else "checks",
                                 "reviewing" if green(state, include_review=False) else "checking")
        if event.action == "publish":
            if state.phase != "locally_ready" or not verified(state):
                raise ValueError("publication requires current readiness")
            return continue_with(state, "approve_publication", "waiting")
        if event.action == "change":
            if not event.evidence_ref:
                raise ValueError("change requires feedback evidence")
            relative_path(event.evidence_ref)
            return continue_with(replace(state, receipts=(), readiness_requested=False),
                                 "implement", "implementing", event.evidence_ref)
        raise ValueError("unsupported feedback action")
    if event.kind == "clarification_answered":
        if state.phase != "waiting" or state.activity not in {"clarify", "approve_publication", "diagnose"}:
            raise ValueError("no matching pending clarification")
        # An answer cannot mutate authority. Scope/grant changes require a proposal.
        return quiescent(state, "waiting")
    if event.kind == "publication_observed":
        if (state.phase != "publishing" or state.activity != "publish" or not verified(state)
                or not {"push", "draft_pr"} <= e.capabilities
                or event.target != (e.repo, e.branch, e.base, state.artifact, "draft")):
            raise ValueError("publication readback does not match ready authorized target")
        return quiescent(state, "completed")
    raise ValueError("illegal event for current state")


class Service:
    """Durable reducer facade, with no scheduling or native lease authority."""

    def __init__(self, journal):
        self.journal = journal

    def read(self, delivery_id):
        version, payload = self.journal.read(delivery_id)
        if payload is None:
            return None
        state = State.from_dict(payload)
        if state.version != version:
            raise ValueError("snapshot version does not match journal")
        return state

    def recorded(self, event):
        record = self.journal.event(event.delivery_id, event.key)
        if record is None:
            return None
        if record.get("fact") != event.to_dict():
            raise ValueError("idempotency key reused with different event")
        return (State.from_dict(record["snapshot"]),
                tuple((key, body) for key, body in record["intents"]))

    def accept(self, event):
        if type(event) is not Event:
            raise ValueError("invalid event")
        recorded = self.recorded(event)
        if recorded is not None:
            return recorded
        try:
            state, intents = apply(self.read(event.delivery_id), event)
            self.journal.append(event.delivery_id, event.expected_version, event.key,
                                state.to_dict(), intents, fact=event.to_dict())
        except (ValueError, RuntimeError):
            # Another connection may have committed this exact fact between
            # lookup and append. Reconcile it without replacing current state.
            recorded = self.recorded(event)
            if recorded is not None:
                return recorded
            raise
        return state, intents

    def pending(self):
        runnable = []
        for key, intent in self.journal.pending():
            state = self.read(intent["delivery"])
            if (state and state.active_intent == key and not state.run_id
                    and state.phase not in {"completed", "cancelled", "stopped"}
                    and intent.get("plan_digest") == state.plan.digest()):
                runnable.append((key, intent))
        return runnable
