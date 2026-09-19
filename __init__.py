"""Hermes native-plugin entrypoint for Software Delivery."""

if __package__:
    from .software_delivery import register
else:  # pytest may import a hyphenated repository root without a package name.
    from software_delivery import register

__all__ = ["register"]
