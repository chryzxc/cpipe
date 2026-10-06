"""The general fixture: each template's seeded bug fails its suite, the obvious fix passes, checks run through quality."""
import os
import subprocess

import pytest

from cpipe import quality
from fixtures.make_repo import TEMPLATES, install, make_repo

FIX = {"node": ("src/calc.js", "a - b", "a + b"), "python": ("calc.py", "a - b", "a + b")}


@pytest.mark.parametrize("kind", sorted(TEMPLATES))
def test_seeded_bug_fails_then_fix_passes_checks(tmp_path, hermes_home, kind):
    fx = make_repo(tmp_path, kind)
    install([fx], hermes_home)
    run = lambda: subprocess.run(TEMPLATES[kind]["test"], shell=True, cwd=fx.repo, capture_output=True)
    assert run().returncode != 0
    path, bug, fix = FIX[kind]
    (fx.repo / path).write_text((fx.repo / path).read_text().replace(bug, fix))
    assert run().returncode == 0
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "fix: add"], cwd=fx.repo,
                   check=True)
    base = quality.diff_base(fx.repo, "main")
    assert base and quality.run_checks(fx.repo, base, fx.project, hermes_home) == []
    (fx.repo / path).write_text("syntax error (\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "break"], cwd=fx.repo,
                   check=True)
    names = [f.split(":")[0] for f in quality.run_checks(fx.repo, base, fx.project, hermes_home)]
    assert names == ["syntax", "tests"]
