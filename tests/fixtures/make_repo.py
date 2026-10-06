"""Throwaway delivery repos built from templates, so cpipe is proven repo-agnostic: nothing is stack-specific.

    make_repo(tmp_path, "node")  ->  Fixture(repo, origin, project, profile, quality)

Each template is a tiny app with a test suite, a lint command, an HTTP server for QA, and one seeded bug
(``add`` subtracts) that fails one test. ``origin`` is a local bare repo, so fetch and push work offline.
The test and lint commands come from the fixture's own ``projects.yaml`` / ``quality.yaml`` entries.

    python tests/fixtures/make_repo.py node /tmp/somewhere     # build one by hand
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

TEMPLATES = {
    "node": {
        "files": {
            "package.json": '{"name": "fixture-node", "private": true, "scripts": {"test": "node --test"}}\n',
            "src/calc.js": "exports.add = (a, b) => a - b;\nexports.mul = (a, b) => a * b;\n",
            "test/calc.test.js": (
                "const test = require('node:test');\nconst assert = require('node:assert');\n"
                "const { add, mul } = require('../src/calc');\n\n"
                "test('add', () => assert.strictEqual(add(2, 3), 5));\n"
                "test('mul', () => assert.strictEqual(mul(2, 3), 6));\n"),
            "server.js": (
                "const http = require('node:http');\nconst { add } = require('./src/calc');\n"
                "http.createServer((req, res) => {\n"
                "  const u = new URL(req.url, 'http://x');\n"
                "  const a = Number(u.searchParams.get('a') || 0), b = Number(u.searchParams.get('b') || 0);\n"
                "  res.setHeader('content-type', 'text/html');\n"
                "  res.end(`<h1>Calc</h1><p id=\"sum\">${a} + ${b} = ${add(a, b)}</p>`);\n"
                "}).listen(process.env.PORT || 3000);\n"),
        },
        "test": "node --test",
        "lint": {"name": "syntax", "files": "*.js", "run": "for f in {files}; do node --check \"$f\" || exit 1; done"},
        "serve": "node server.js",
    },
    "python": {
        "files": {
            "calc.py": "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n",
            "test_calc.py": (
                "import unittest\n\nfrom calc import add, mul\n\n\n"
                "class CalcTest(unittest.TestCase):\n"
                "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n"
                "    def test_mul(self):\n        self.assertEqual(mul(2, 3), 6)\n"),
            "server.py": (
                "import os\nfrom http.server import BaseHTTPRequestHandler, HTTPServer\n"
                "from urllib.parse import parse_qs, urlparse\n\nfrom calc import add\n\n\n"
                "class H(BaseHTTPRequestHandler):\n"
                "    def do_GET(self):\n"
                "        q = parse_qs(urlparse(self.path).query)\n"
                "        a, b = int(q.get('a', [0])[0]), int(q.get('b', [0])[0])\n"
                "        self.send_response(200)\n        self.send_header('content-type', 'text/html')\n"
                "        self.end_headers()\n"
                "        self.wfile.write(f'<h1>Calc</h1><p id=\"sum\">{a} + {b} = {add(a, b)}</p>'.encode())\n\n\n"
                "HTTPServer(('127.0.0.1', int(os.environ.get('PORT', 8000))), H).serve_forever()\n"),
        },
        "test": "python3 -B -m unittest -q",
        "lint": {"name": "syntax", "files": "*.py", "run": "python3 -m py_compile {files}"},
        "serve": "python3 -B server.py",
    },
}

TASK = "The calculator's add returns the wrong result (2 + 3 shows -1). Fix it."


@dataclass
class Fixture:
    repo: Path      # the main checkout
    origin: Path    # bare remote
    project: str
    profile: dict   # this project's projects.yaml entry
    quality: dict   # quality.yaml with this project's checks


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=fixture@cpipe", "-c", "user.name=fixture", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


def make_repo(root: Path, kind: str = "node", name: str | None = None) -> Fixture:
    t = TEMPLATES[kind]
    project = name or f"fixture-{kind}"
    repo, origin = root / project, root / f"{project}.git"
    for rel, text in t["files"].items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    (repo / ".gitignore").write_text("__pycache__/\nnode_modules/\n.cpipe/\n")
    (repo / "AGENTS.md").write_text(f"# {project}\n\nTests: `{t['test']}`. Serve: `{t['serve']}` ($PORT).\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    _git(root, "clone", "-q", "--bare", str(repo), str(origin))
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "fetch", "-q", "origin")
    _git(repo, "branch", "-q", "--set-upstream-to=origin/main", "main")
    profile = {"repo": str(repo), "base_branch": "main", "qa": "browser",
               "conventions": f"Run `{t['test']}` before committing.",
               "test_env": {"services": {"web": t["serve"]}}}
    checks = [{**t["lint"], "projects": [project]},
              {"name": "tests", "run": t["test"], "projects": [project]}]
    return Fixture(repo, origin, project, profile, {"checks": checks})


def install(fixtures: list[Fixture], hermes_home: Path) -> None:
    """Write the fixtures' projects.yaml and quality.yaml into a (temp) HERMES_HOME."""
    delivery = hermes_home / "delivery"
    delivery.mkdir(parents=True, exist_ok=True)
    (delivery / "projects.yaml").write_text(yaml.safe_dump({"projects": {f.project: f.profile for f in fixtures}}))
    (delivery / "quality.yaml").write_text(yaml.safe_dump({"checks": [c for f in fixtures for c in f.quality["checks"]]}))


if __name__ == "__main__":
    out = make_repo(Path(sys.argv[2]), sys.argv[1])
    print(out.repo)
