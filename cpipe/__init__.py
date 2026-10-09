"""cpipe v2 only: frozen local envelopes, observed checks and independent review."""

__version__ = '2.0.0'


def register(ctx):
    from . import native
    from .runtime.cli import handle, setup_parser
    native.register(ctx)
    ctx.register_cli_command(name='cpipe', help='cpipe v2 local delivery controller (no legacy daemon)',
        setup_fn=setup_parser, handler_fn=handle,
        description='Inspect v2 state and verify the installed native adapters.')
