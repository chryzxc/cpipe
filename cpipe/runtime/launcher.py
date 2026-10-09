"""Child launch through the loaded Hermes installation's managed bootstrap."""
from pathlib import Path


def module_command(module: str, *args: str) -> list[str]:
    try:
        import hermes_cli
        from hermes_cli._launchers import runtime_command
    except ImportError as exc:
        raise RuntimeError('The loaded Hermes installation has no supported runtime bootstrap') from exc
    from cpipe import home
    root = Path(hermes_cli.__file__).resolve().parent.parent
    package = Path(__file__).resolve().parents[2]
    code = (f'sys.path.insert(0, {str(package)!r}); import runpy; '
            f'sys.argv = [{module!r}, *sys.argv[1:]]; '
            f'runpy.run_module({module!r}, run_name="__main__", alter_sys=True)')
    return runtime_command(root, code=code, args=args or [], home=str(home.root()))
