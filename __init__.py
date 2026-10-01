"""Hermes native-plugin entrypoint for cpipe."""

if __package__:
    from .cpipe import register
else:  # pytest may import a hyphenated repository root without a package name.
    from cpipe import register

__all__ = ["register"]
