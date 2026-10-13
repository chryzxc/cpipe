"""Run every entry point on the plugin code as it is on disk now, so an updated plugin reaches chats and
runs that are already open. An updated copy loads beside the old one under its own package name; a copy
that fails to import (a half-written update) is skipped until the files settle."""
import hashlib
import importlib
import importlib.util
import logging
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_LOCK = threading.Lock()
_COPIES = {}  # files digest -> package name (None when that version failed to load)
_GOOD = [None]  # the newest copy that loaded
log = logging.getLogger('cpipe')


def files():
    """Cheap fingerprint of the plugin source: path, size and modification time of every module."""
    digest = hashlib.sha256()
    for path in sorted(ROOT.rglob('*.py')):
        if '__pycache__' not in path.parts:
            stat = path.stat()
            digest.update(f'{path.relative_to(ROOT)}:{stat.st_size}:{stat.st_mtime_ns}\n'.encode())
    return digest.hexdigest()


LOADED = files()


def _load(digest):
    name = f'{__package__}_live_{digest[:12]}'
    spec = importlib.util.spec_from_file_location(name, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    try:
        spec.loader.exec_module(package)
        native = importlib.import_module(name + '.native')
        native.preload()
        if files() != digest:
            raise ImportError('the plugin files changed while loading')
    except BaseException:
        for key in [k for k in sys.modules if k == name or k.startswith(name + '.')]:
            del sys.modules[key]
        raise
    return name


def module(short):
    """The newest loadable copy of cpipe.<short>, or this process's own when nothing changed."""
    own = sys.modules[f'{__package__}.{short}']
    try:
        digest = files()
    except OSError:
        return own
    if digest == LOADED:
        return own
    with _LOCK:
        if digest not in _COPIES:
            try:
                _COPIES[digest] = _GOOD[0] = _load(digest)
                log.info('cpipe: loaded the updated plugin code for open chats and runs')
                mine, fresh = sys.modules[f'{__package__}.native'], sys.modules[_COPIES[digest] + '.native']
                # One process, one set of runs, one chat context, whichever copy serves the call.
                fresh._CTX, fresh._RUNS, fresh._SCANNED = mine._CTX, mine._RUNS, mine._SCANNED
            except Exception:
                _COPIES[digest] = None
                log.warning('cpipe: the updated plugin code does not load yet; using the loaded code', exc_info=True)
        name = _COPIES[digest] or _GOOD[0]  # a broken update keeps the newest working code; retried once it changes
    if name is None:
        return own
    return importlib.import_module(f'{name}.{short}')


def entry(short, function):
    """A host callback that always runs the newest copy of cpipe.<short>.<function>."""
    def call(*args, **kwargs):
        return getattr(module(short), function)(*args, **kwargs)
    call.__name__ = function
    return call
