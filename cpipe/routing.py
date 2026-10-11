"""Observe saved Hermes profiles; no external CLIs."""


def profile_home(profile):
    from hermes_cli.profiles import get_profile_dir
    return get_profile_dir(profile)
