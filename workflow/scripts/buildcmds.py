#!/usr/bin/env python3
"""Detect build/test commands for a workspace. Deterministic, no LLM."""
import json
from pathlib import Path


def detect_build(workspace):
    """Return {stack, build, test, filter_flag} for a workspace, or None if unsupported.

    build may be None (stack has no warm-build step). filter_flag is the CLI flag
    that scopes the test run to one test, or None when the stack has none.
    """
    ws = Path(workspace)
    if (ws / 'Package.swift').is_file():
        return {
            'stack': 'swift',
            'build': ['swift', 'build', '--disable-automatic-resolution'],
            'test': ['swift', 'test', '--disable-automatic-resolution'],
            'filter_flag': '--filter',
        }
    if (ws / 'pyproject.toml').is_file() or (ws / 'setup.cfg').is_file():
        return {
            'stack': 'python',
            'build': None,
            'test': ['python3', '-m', 'pytest'],
            'filter_flag': '-k',
        }
    pkg = ws / 'package.json'
    if pkg.is_file():
        try:
            scripts = json.loads(pkg.read_text()).get('scripts', {})
        except (json.JSONDecodeError, OSError):
            return None
        if 'test' in scripts:
            build = ['npm', 'run', 'build'] if 'build' in scripts else None
            return {'stack': 'node', 'build': build, 'test': ['npm', 'test'], 'filter_flag': None}
        return None
    makefile = ws / 'Makefile'
    if makefile.is_file():
        try:
            has_test = 'test:' in makefile.read_text(errors='ignore')
        except OSError:
            return None
        if has_test:
            return {'stack': 'make', 'build': None, 'test': ['make', 'test'], 'filter_flag': None}
    return None
