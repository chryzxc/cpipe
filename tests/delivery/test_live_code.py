"""An updated plugin reaches chats and runs that are already open, without a restart or a new chat."""
import importlib
import shutil
import sys
import time
from pathlib import Path

import cpipe


def copy(tmp_path, monkeypatch):
    root = tmp_path / 'plugin'
    shutil.copytree(Path(cpipe.__file__).parent, root / 'cpipe_copy', ignore=shutil.ignore_patterns('__pycache__'))
    monkeypatch.syspath_prepend(str(root))
    native = importlib.import_module('cpipe_copy.native')
    return root / 'cpipe_copy', native, importlib.import_module('cpipe_copy.live')


def edit(path, text):
    time.sleep(.01)  # a distinct modification time even on coarse clocks
    path.write_text(path.read_text() + text)


def test_entry_points_use_the_plugin_code_on_disk(tmp_path, monkeypatch):
    root, native, live = copy(tmp_path, monkeypatch)
    try:
        assert live.module('native') is native  # nothing changed: the loaded code
        native._CTX = object()
        edit(root / 'native.py', '\nMARK = "updated"\n')
        fresh = live.module('native')
        assert fresh is not native and fresh.MARK == 'updated'
        # One process keeps one set of runs and one chat context, whichever copy serves the call.
        assert fresh._RUNS is native._RUNS and fresh._CTX is native._CTX and fresh._SCANNED is native._SCANNED
        assert live.entry('native', 'execute')({}) == fresh.execute({})
        assert live.module('native') is fresh  # loaded once per version
        edit(root / 'delivery' / 'service.py', '\nthis is not python\n')  # a half-written update
        assert live.module('native') is fresh  # keeps the newest working code
        edit(root / 'delivery' / 'service.py', '\n')  # still broken: tried once per version, never crashes a call
        assert live.module('native') is fresh
    finally:
        for name in [n for n in sys.modules if n.startswith('cpipe_copy')]:
            del sys.modules[name]
