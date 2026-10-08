"""Child launch through Hermes' supported dependency bootstrap.

Neither PATH's first ``hermes`` nor inherited PYTHONPATH identifies the running
installation. Bootstrap the loaded Hermes tree, then add this installed plugin's
absolute import root. Probe, daemon, supervisor and round share this exact path.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


def module_command(module: str, *args: str) -> list[str]:
    try:
        import hermes_cli
        from hermes_cli._launchers import runtime_command
    except ImportError as exc:
        raise RuntimeError("The loaded Hermes installation has no supported runtime bootstrap") from exc
    from cpipe import home

    root = Path(hermes_cli.__file__).resolve().parent.parent
    package = Path(__file__).resolve().parents[2]
    code = (f"sys.path.insert(0, {str(package)!r}); import runpy; "
            f"sys.argv = [{module!r}, *sys.argv[1:]]; "
            f"runpy.run_module({module!r}, run_name='__main__', alter_sys=True)")
    return runtime_command(root, code=code, args=args or [], home=str(home.root()))


def preflight() -> dict:
    """Import-only check through the real launch path, without repository work.

    Hermes owns dependency provisioning at bootstrap. Do not pip-install into an
    arbitrary interpreter or silently run an update when that bootstrap fails.
    """
    from .envpolicy import redact
    try:
        result = subprocess.run(module_command("cpipe.runtime.preflight"), capture_output=True,
                                text=True, timeout=60, cwd=Path(__file__).resolve().parents[2])
        receipt = json.loads(result.stdout.splitlines()[-1]) if result.stdout.strip() else {}
        if result.returncode == 0 and receipt.get("ready") is True:
            return receipt
        failures = receipt.get("failures") or []
        modules = ", ".join(str(f.get("module", "runtime")) for f in failures)
        detail = modules or redact(result.stderr[-2000:], os.environ) or "missing preflight receipt"
        raise RuntimeError(f"cpipe runtime preflight failed ({detail}); repair the installed Hermes/plugin dependencies and retry")
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise RuntimeError(f"cpipe runtime preflight failed: {redact(str(exc), os.environ)}") from exc
