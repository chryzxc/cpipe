"""``python -m cpipe.round``: one delivery round (graph steps up to an interrupt), run like a command.

The round writes ``{"status", "summary", "metadata"}`` to its result file; the supervisor applies it.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
import tempfile
from pathlib import Path

from ..result import Status, WorkerResult
from ..launcher import module_command
from .base import LaunchContext
from .command import CommandAdapter


class RoundAdapter(CommandAdapter):
    def available(self) -> tuple[bool, str]:
        """The lane's engine (the lane is named after it) is installed."""
        from ... import engines, settings
        try:
            cfg = settings.load()
        except ValueError as e:
            return (False, str(e))
        return engines.available(self.lane.name, cfg["engines"].get(self.lane.name) or {})

    def launch(self, ctx: LaunchContext) -> None:
        self._result = Path(tempfile.mkstemp(prefix=f"cpipe-{ctx.task_id}-", suffix=".json")[1])
        self.lane = replace(self.lane, command=tuple(module_command(
            "cpipe.round", ctx.task_id, "--result", str(self._result))))
        super().launch(ctx)

    def collect_result(self) -> WorkerResult:
        fallback = super().collect_result()
        try:
            data = json.loads(self._result.read_text() or "{}")
        except (OSError, ValueError):
            data = {}
        finally:
            self._result.unlink(missing_ok=True)
        if fallback.status is Status.CANCELLED or not data.get("status"):
            return fallback  # cancelled, or the round crashed before writing: its output tail says why
        meta = {**fallback.metadata, **(data.get("metadata") or {})}
        if data.get("status") == "blocked" and meta.get("cpipe", {}).get("node") == "paused":
            meta["cpipe"]["runtime_failure"] = "transient"
        return WorkerResult(Status(data["status"]), data.get("summary", ""), meta, exit_code=fallback.exit_code)
