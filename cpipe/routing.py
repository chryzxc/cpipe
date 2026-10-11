"""Observe saved Hermes profiles; no external CLIs."""


def profile_home(profile):
    from hermes_cli.profiles import get_profile_dir
    return get_profile_dir(profile)


def soul(profile):
    """(one-line role from the profile's SOUL.md, whether it declares itself read-only)."""
    import re
    try:
        text = (profile_home(profile) / 'SOUL.md').read_text(errors='replace')
    except (OSError, ValueError):
        return '', False
    lines = [l.strip(' #*>-') for l in text.splitlines() if l.strip(' #*>-')]
    line = next((l for l in lines if l.lower().startswith('you are')), lines[0] if lines else '')
    # Keyword heuristic; a builder that still refuses at run time hands over to the next one.
    return re.sub(r'[*_`]', '', line).split('. ')[0][:120], bool(re.search(r'read-only|readonly|no edits', text, re.I))
