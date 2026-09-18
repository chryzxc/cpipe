from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "SKILL.md",
    "references/routing.md",
    "references/route-recipes.md",
    "references/team-config.yaml",
    "references/handoffs.md",
    "references/quality-gates.md",
    "references/task-graph-flow.md",
    "references/native-bot-dispatch.md",
    "references/parallel-execution.md",
    "references/security-lifecycle.md",
    "references/acceptance-scenarios.json",
}

errors: list[str] = []
for rel in sorted(REQUIRED):
    if not (ROOT / rel).is_file():
        errors.append(f"missing:{rel}")

skill = (ROOT / "SKILL.md").read_text()
if not skill.startswith("---\n"):
    errors.append("frontmatter:not-at-byte-zero")
if "name: my-software-delivery-orchestrator" not in skill:
    errors.append("frontmatter:wrong-name")
if "version: 7.0.0" not in skill:
    errors.append("frontmatter:wrong-version")
if "sole software-delivery control plane" not in skill:
    errors.append("scope:missing-control-plane")

combined = "\n".join(
    (ROOT / rel).read_text()
    for rel in REQUIRED
    if rel.endswith((".md", ".yaml")) and (ROOT / rel).is_file()
)
for phrase in (
    "authorization basis",
    "exact target allowlist",
    "engagement start/end window",
    "per-host rate limit",
    "destructive testing approval",
    "production testing approval",
    "CONFIRMED",
    "NOT_REPRODUCIBLE",
    "PARTIAL",
    "BLOCKED",
):
    if phrase not in combined:
        errors.append(f"security-contract:missing:{phrase}")

for forbidden in (
    "Release Engineer deploy",
    "automatic active testing",
    "Security Tester may remediate",
):
    if forbidden in combined:
        errors.append(f"forbidden:{forbidden}")

scenarios = json.loads((ROOT / "references/acceptance-scenarios.json").read_text())
ids = [row["id"] for row in scenarios]
if len(ids) != len(set(ids)):
    errors.append("scenarios:duplicate-id")
if len(scenarios) != 8:
    errors.append(f"scenarios:expected-8-found-{len(scenarios)}")

if errors:
    print("FAIL")
    print("\n".join(errors))
    sys.exit(1)
print("PASS: software-delivery policy structure and security contract")
