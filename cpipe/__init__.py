"""cpipe: one card per delivery, driven through a fixed graph by the bundled runtime.

``hermes cpipe run`` is the daemon (its own process, never inside the gateway). It claims ``cp:<engine>``
cards and runs one round per claim through the selected content/small/large path. Builder sessions persist;
planning, QA and review can use separate Hermes profiles. Explicit slash commands (``tools.py``) start and steer
deliveries; model-callable tools are opt-in.
"""

from __future__ import annotations


def register(ctx) -> None:
    from . import tools
    from .runtime.cli import handle, setup_parser

    from .runtime.dispatch_guard import install
    install()

    tools.register(ctx)

    ctx.register_cli_command(
        name="cpipe",
        help="Delivery runner: claims cp:* cards and drives each delivery to a tested, reviewed pull request",
        setup_fn=setup_parser,
        handler_fn=handle,
        description="Run, inspect and check the cpipe daemon.",
    )
