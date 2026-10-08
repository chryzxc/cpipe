"""Test-only bootstrap substitute for throwaway homes.

The real managed bootstrap is tested separately. Unit/E2E fixtures use the test
interpreter and explicit roots, never provision hundreds of managed generations.
The substitute propagates only inside fixture subprocesses, not production code.
"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def runtime_command(root, *, code, args=(), home=None, **kwargs):
    setup = (f"import sys, os; sys.path.insert(0, {str(root)!r}); "
             f"sys.path.insert(0, {str(ROOT / 'tests/runtime')!r}); "
             f"os.environ['HERMES_HOME'] = {str(home)!r}; "
             "import fake_launcher; fake_launcher.install(); ")
    return [sys.executable, "-c", setup + code, *args]


def install():
    from hermes_cli import _launchers
    _launchers.runtime_command = runtime_command


if __name__ == "__main__":
    import runpy
    if sys.argv[1] != "--run-module":
        raise SystemExit("test launcher only supports --run-module")
    module, sys.argv = sys.argv[2], [sys.argv[2], *sys.argv[3:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
