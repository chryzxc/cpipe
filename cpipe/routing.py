"""Explicit delivery-local routing. Saved defaults are candidates, never consent."""
from __future__ import annotations

from . import engines

MODES = {
    "Specialist bots/profiles": "specialists",
    "External coding CLI": "external",
    "Single Hermes profile (default or another)": "single",
}
ROLES = ("plan", "build", "review", "qa")


def profile_home(profile):
    from .home import root
    home = root()
    base = home.parent.parent if home.parent.name == "profiles" else home
    return base if profile == "default" else base / "profiles" / profile


def configured_model(ec):
    """Observe only the profile model identifier; never credentials or endpoints."""
    if ec.get("kind") != "hermes" or ec.get("model"):
        return None
    import yaml
    try:
        cfg = yaml.safe_load((profile_home(ec.get("profile") or "default") / "config.yaml").read_text())
        model = (cfg.get("model") or {}).get("default")
        return model if isinstance(model, str) and model.strip() else None
    except (OSError, ValueError, AttributeError, yaml.YAMLError):
        return None


def candidates(cfg, mode):
    result = {}
    for name, ec in cfg["engines"].items():
        kind = engines.kind(name, ec)
        if (kind == "hermes") != (mode == "single"):
            continue
        if kind not in engines.KINDS or not engines.available(name, ec)[0]:
            continue
        if kind == "hermes":
            profile = ec.get("profile") or "default"
            if profile != "default" and not (profile_home(profile) / "config.yaml").is_file():
                continue
        result[name] = dict(ec)
    return result


def specialist_candidates(cfg):
    """Discover installed profiles without changing configuration or their models."""
    ready = candidates(cfg, "single")
    from .home import root
    home = root()
    base = home.parent.parent if home.parent.name == "profiles" else home
    profiles = base / "profiles"
    represented = {ec.get("profile") or "default" for ec in ready.values()}
    if profiles.is_dir():
        for file in sorted(profiles.glob("*/config.yaml")):
            profile = file.parent.name
            name = "profile:" + profile
            ec = {"kind": "hermes", "profile": profile, "bin": "hermes"}
            if profile not in represented and name not in ready and engines.available(name, ec)[0]:
                ready[name] = ec
    return ready


def selected_specialists(cfg, ready, selected):
    """Freeze explicit owners onto the existing Hermes builder lane."""
    if len(selected) != len(ROLES) or any(name not in ready for name in selected):
        raise ValueError("Select an available Hermes owner for every stage. No work started.")
    if (ready[selected[1]].get("profile") or "default") == (ready[selected[2]].get("profile") or "default"):
        raise ValueError("Select different build and review profiles for independent review. No work started.")
    roles = {role: "hermes" if role == "build" else "route-" + role for role in ROLES}
    if cfg.get("roles", {}).get("discover"):
        roles["discover"] = cfg["roles"]["discover"]
    local = {**cfg, "roles": roles, "engines": {**cfg["engines"],
             **{roles[role]: {**ready[name], "kind": "hermes"} for role, name in zip(ROLES, selected)}}}
    return snapshot(local, "specialists", "hermes", roles)


def specialist_form(ready, error=None):
    if len({ec.get("profile") or "default" for ec in ready.values()}) < 2:
        raise ValueError("Specialist routing needs at least two available Hermes profiles for independent review. Choose single-profile mode or install another profile. No work started.")
    import secrets
    descriptions = "; ".join(f"{name}: {ec.get('profile') or 'default'} ({ec.get('model') or configured_model(ec) or 'profile default'})"
                             for name, ec in ready.items())
    return {"questions": [{"question": f"[cpipe-owner {secrets.token_hex(12)}] Select the {role} owner. "
             + (str(error) + " " if error else "")
             + "Build and review must use different profiles. Available owners: " + descriptions,
             **({"choices": list(ready)} if len(ready) <= 4 else {})} for role in ROLES]}


def specialist_route(cfg):
    roles = cfg.get("roles") or {}
    ready = candidates(cfg, "single")
    if any(roles.get(role) not in ready for role in ROLES):
        raise ValueError("Specialist routing needs available Hermes engines assigned to plan/build/review/qa in cpipe.yaml. No work started.")
    builder = ready[roles["build"]].get("profile") or "default"
    reviewer = ready[roles["review"]].get("profile") or "default"
    if builder == reviewer:
        raise ValueError("Specialist routing needs different build and review profiles; choose single-profile mode otherwise.")
    return snapshot(cfg, "specialists", roles["build"], {role: roles[role] for role in ROLES})


def snapshot(cfg, mode, engine, roles=None):
    if mode not in ("single", "external", "specialists"):
        raise ValueError("unknown routing mode")
    if mode != "specialists" and engine not in candidates(cfg, mode):
        raise ValueError("Selected execution engine is unavailable or does not match the route. No work started.")
    roles = dict(roles or {role: engine for role in ROLES})
    if cfg.get("roles", {}).get("discover"):
        roles["discover"] = cfg["roles"]["discover"] if mode == "specialists" else engine
        if mode == "specialists" and roles["discover"] not in candidates(cfg, "single"):
            raise ValueError("Configured discovery profile is unavailable; no work started.")
    configs = {name: dict(cfg["engines"][name]) for name in set(roles.values())}
    # A QA-specific profile must not silently break the user's selected ownership.
    for name, ec in configs.items():
        ec.pop("qa_profile", None)
        ec["kind"] = engines.kind(name, ec)
        if model := configured_model(ec):
            ec["observed_model"] = model
    return {"mode": mode, "engine": engine, "roles": roles, "engines": configs,
            "review_kind": "specialist review" if mode == "specialists" else "same-profile review" if mode == "single" else "same-backend review"}


def describe(route):
    lines = []
    for role, name in route["roles"].items():
        ec = route["engines"][name]
        kind = engines.kind(name, ec)
        owner = f"Hermes profile {ec.get('profile') or 'default'}" if kind == "hermes" else f"external {kind} CLI"
        model = ec.get("model") or (f"{ec['observed_model']} (observed profile default)" if ec.get("observed_model") else "profile/CLI default, unresolved")
        lines.append(f"{role}: {name} ({owner}; model: {model})")
    return "\n".join(lines) + f"\nReview: {route['review_kind']}. Automatic fallback is disabled for this delivery."


def mode_form(payload, nonce):
    size = payload.get("size", "large")
    recommendation = ("Specialists are recommended for this larger task: separate planning and review ownership."
                      if size == "large" else "One Hermes profile is recommended for this bounded task: fewer handoffs.")
    return {"questions": [{"question": f"[cpipe-route {nonce}] How should this delivery run?\n\n{payload.get('request', '')}\n\n"
            + recommendation + "\nSpecialists: role-specific Hermes skills and separate review; more setup and handoffs."
            + "\nExternal CLI: focused repository workflow; uses its own authentication, permissions and instructions, not Hermes skills."
            + "\nSingle profile: the default or one selected Hermes profile owns every stage; simpler continuity, but no independent specialist review."
            + "\nSpeed and cost depend on the task and configured models; no guaranteed savings. Unavailable routes will not start work.",
            "choices": list(MODES) if size == "large" else [list(MODES)[2], *list(MODES)[:2]]}]}


def effective_config(cfg, route):
    """Freeze stage owners across resumes; do not inherit later global defaults."""
    return {**cfg, "engines": route["engines"], "roles": route["roles"],
            "default_engine": route["engine"], "review_engine": route["roles"]["review"], "fallback_engine": None,
            "role_overrides": {}}
