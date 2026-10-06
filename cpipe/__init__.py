"""cpipe: one card per delivery, driven through a fixed graph by the bundled runtime.

``hermes cpipe run`` is the daemon (its own process, never inside the gateway). It claims ``cp:<engine>``
cards and runs one round per claim: build ⇄ checks → QA → review → PR, in one engine session.
"""

from __future__ import annotations


def register(ctx) -> None:
    from .runtime.cli import handle, setup_parser

    ctx.register_cli_command(
        name="cpipe",
        help="Delivery runner: claims cp:* cards and drives each delivery to a tested, reviewed pull request",
        setup_fn=setup_parser,
        handler_fn=handle,
        description="Run, inspect and check the cpipe daemon.",
    )
