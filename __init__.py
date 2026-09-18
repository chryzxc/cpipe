"""Hermes native-plugin entrypoint for Delivery Workflow."""

if __package__:
    from .delivery_workflow import register
else:  # pytest may import a hyphenated repository root without a package name.
    from delivery_workflow import register

__all__ = ["register"]
