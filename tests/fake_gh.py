#!/usr/bin/env python3
"""A stand-in for ``gh`` (installed on PATH as ``gh``): a GitHub repo with one pull request.

$FAKE_GH_CHECKS is what ``pr checks`` prints. Every call is appended to $FAKE_GH_LOG as its argv.
"""
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
if args[:2] == ["repo", "view"]:
    print(json.dumps({"url": "https://github.com/acme/app"}))
elif args[:2] == ["pr", "create"]:
    print("https://github.com/acme/app/pull/7")
elif args[:2] == ["pr", "checks"]:
    print(os.environ.get("FAKE_GH_CHECKS", "[]"))
elif args[:2] == ["run", "view"]:
    print("FAIL test_add: expected 5")
