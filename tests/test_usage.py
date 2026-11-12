"""Synthetic SQLite and launch logs only: no real sessions or model calls."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/run/bin/cpipe.py'
spec = importlib.util.spec_from_file_location('usage_facts', SCRIPT)
facts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(facts)


@pytest.fixture
def usage(tmp_path, monkeypatch):
    root, tree, run = (tmp_path / name for name in ('home', 'work', 'run'))
    for p in (root, tree, run, run / 'logs'):
        p.mkdir(exist_ok=True)
    monkeypatch.setenv('HERMES_HOME', str(root))
    monkeypatch.setenv('HERMES_SESSION_ID', 'chat')
    (run / 'chats').write_text('chat\n')

    def store(profile='default'):
        path = root / 'state.db' if profile == 'default' else root / 'profiles' / profile / 'state.db'
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.executescript('''CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, profile_name TEXT, model TEXT, cwd TEXT, started_at REAL,
            parent_session_id TEXT, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0,
            api_call_count INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS session_model_usage (
            session_id TEXT, model TEXT, billing_provider TEXT, task TEXT,
            input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
            cache_write_tokens INTEGER, api_call_count INTEGER);''')
        return db

    def session(db, ident, model='m', cwd=None, started=0, parent=None):
        db.execute('INSERT INTO sessions(id,model,cwd,started_at,parent_session_id) VALUES (?,?,?,?,?)',
                   (ident, model, str(cwd or tree), started, parent))
        db.commit()

    def row(db, ident, model='m', task='', values=(100, 10, 900, 20, 1)):
        db.execute('INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?)',
                   (ident, model, 'provider', task, *values))
        db.commit()

    def log(ident, profile='builder', name='build-1'):
        (run / 'logs' / (name + '.txt')).write_text(
            f'session_id: {ident}\ncpipe_profile: {profile}\ncpipe_resume: new\nexit 0\n')

    return root, tree, run, store, session, row, log


def cells(text, who):
    return [[s.strip() for s in line.split('|')[1:-1]]
            for line in text.splitlines() if line.startswith('| ' + who + ' |')]


def test_ledger_switch_auxiliary_and_exact_totals(usage):
    root, tree, run, store, session, row, log = usage
    db = store(); session(db, 'chat'); row(db, 'chat')
    facts.cost(run, tree, start=True)
    db.execute('UPDATE session_model_usage SET input_tokens=120, output_tokens=15, '
               'cache_read_tokens=1080, cache_write_tokens=23, api_call_count=3')
    db.commit()
    row(db, 'chat', 'new-model', values=(7, 8, 9, 2, 1))
    row(db, 'chat', 'compressor', 'compression', (3, 4, 5, 6, 1))
    # Session counters deliberately overlap: they must not be added to the ledger.
    db.execute('UPDATE sessions SET input_tokens=999999, model="new-model"'); db.commit()
    text = facts.cost(run, tree)
    main = cells(text, 'this chat')
    assert any(r[1:4] == ['m', 'provider', 'main'] and r[4:] == ['20', '180', '3', '5', '2'] for r in main)
    assert any('compression' in r and r[1] == 'compressor' for r in main)
    assert cells(text, '**Total**')[0][4:] == ['30', '194', '11', '17', '4']
    assert '999999' not in text and 'since capture' in text
    db.close()


def test_old_resumed_role_and_same_id_other_profile_not_unrelated_cwd(usage):
    root, tree, run, store, session, row, log = usage
    db = store(); session(db, 'chat'); row(db, 'chat')
    worker = store('builder'); session(worker, 'chat'); row(worker, 'chat')
    session(worker, 'unrelated'); row(worker, 'unrelated')
    session(worker, 'neighbor', cwd=Path(str(tree) + '-other')); row(worker, 'neighbor')
    facts.cost(run, tree, start=True)
    worker.execute('UPDATE session_model_usage SET input_tokens=input_tokens+11'); worker.commit()
    log('chat')
    text = facts.cost(run, tree)
    assert cells(text, '@builder')[0][4:] == ['11', '0', '0', '0', '0']
    assert cells(text, '**Total**')[0][4] == '11'
    snapshot = json.loads((run / 'usage-start.json').read_text())
    assert 'neighbor' not in snapshot['stores']['builder']['sessions']
    db.close(); worker.close()


def test_new_role_zero_baseline_but_old_late_role_partial(usage):
    root, tree, run, store, session, row, log = usage
    db = store(); session(db, 'chat'); row(db, 'chat')
    worker = store('builder')
    facts.cost(run, tree, start=True)
    session(worker, 'new', started=time.time()); row(worker, 'new', values=(5, 6, 7, 8, 1)); log('new')
    session(worker, 'old', started=0); row(worker, 'old'); log('old', name='review-1')
    text = facts.cost(run, tree)
    assert cells(text, '@builder')[0][4:] == ['5', '7', '8', '6', '1']
    assert 'missing baseline' in text and 'partial' in text.lower()
    db.close(); worker.close()


def test_capture_append_preserves_baselines_and_is_idempotent(usage):
    root, tree, run, store, session, row, log = usage
    db = store(); session(db, 'chat'); row(db, 'chat')
    facts.cost(run, tree, start=True)
    first = (run / 'usage-start.json').read_bytes()
    facts.cost(run, tree, start=True)
    assert (run / 'usage-start.json').read_bytes() == first
    session(db, 'later'); row(db, 'later')
    facts.cost(run, tree, start=True)
    latest = json.loads((run / 'usage-start.json').read_text())
    original = json.loads(first)
    assert latest['stores']['default']['sessions']['chat'] == original['stores']['default']['sessions']['chat']
    assert 'later' in latest['stores']['default']['sessions']
    db.close()


@pytest.mark.parametrize('failure', ['reset', 'missing-session', 'missing-store', 'corrupt-store', 'locked-store'])
def test_unmeasurable_usage_is_partial_not_zero(usage, failure):
    root, tree, run, store, session, row, log = usage
    db = store(); session(db, 'chat'); row(db, 'chat')
    facts.cost(run, tree, start=True)
    if failure == 'reset':
        db.execute('UPDATE session_model_usage SET input_tokens=1')
    if failure == 'missing-session':
        db.execute('DELETE FROM sessions')
    db.commit(); db.close()
    if failure == 'missing-store':
        (root / 'state.db').unlink()
    if failure == 'corrupt-store':
        (root / 'state.db').write_text('not sqlite')
    locked = None
    if failure == 'locked-store':
        locked = sqlite3.connect(root / 'state.db')
        locked.execute('BEGIN EXCLUSIVE')
    text = facts.cost(run, tree)
    if locked:
        locked.rollback(); locked.close()
    assert 'partial' in text.lower()
    assert not cells(text, 'this chat')


@pytest.mark.parametrize('payload', ['not json', '{}', '{"version":2,"stores":[]}'])
def test_bad_snapshot_is_visible_and_never_overwritten(usage, payload):
    root, tree, run, *_ = usage
    (run / 'usage-start.json').write_text(payload)
    assert 'partial' in facts.cost(run, tree).lower()
    assert 'partial' in facts.cost(run, tree, start=True).lower()
    assert (run / 'usage-start.json').read_text() == payload


def test_profile_coordinator_and_compacted_child(usage, monkeypatch):
    root, tree, run, store, session, row, log = usage
    monkeypatch.setenv('HERMES_HOME', str(root / 'profiles' / 'builder'))
    db = store('builder'); session(db, 'chat'); row(db, 'chat')
    facts.cost(run, tree, start=True)
    db.execute('UPDATE session_model_usage SET input_tokens=input_tokens+5'); db.commit()
    assert cells(facts.cost(run, tree), 'this chat')[0][4] == '5'
    session(db, 'child', started=time.time(), parent='chat'); row(db, 'child')
    monkeypatch.setenv('HERMES_SESSION_ID', 'child')
    (run / 'chats').write_text('chat\nchild\n')
    text = facts.cost(run, tree)
    assert 'inherited counters' in text
    assert cells(text, '**Total**')[0][4] == '5'
    db.close()


def test_unknown_log_membership_is_not_silently_omitted(usage):
    root, tree, run, store, session, row, log = usage
    db = store(); session(db, 'chat'); row(db, 'chat'); facts.cost(run, tree, start=True)
    (run / 'logs' / 'build-1.txt').write_text('session_id: worker\nexit 0\n')
    assert 'identity' in facts.cost(run, tree).lower()
    db.close()


def test_cost_cli_and_worktree_guard(usage):
    root, tree, run, store, session, row, log = usage
    db = store(); session(db, 'chat'); row(db, 'chat'); db.close()
    result = subprocess.run([sys.executable, str(SCRIPT), 'cost', str(run), str(tree), '--start'],
                            capture_output=True, text=True, check=True)
    assert 'capture' in result.stdout
    result = subprocess.run([sys.executable, str(SCRIPT), 'cost', str(run), str(tree)],
                            capture_output=True, text=True, check=True)
    assert 'since capture' in result.stdout
    assert 'worktree' in facts.cost(run, root).lower()


def test_resumed_session_created_after_capture_still_needs_baseline(usage):
    root, tree, run, store, session, row, log = usage
    db = store(); session(db, 'chat'); row(db, 'chat')
    worker = store('builder'); facts.cost(run, tree, start=True)
    session(worker, 'resumed', started=time.time()); row(worker, 'resumed'); log('resumed')
    path = run / 'logs' / 'build-1.txt'
    path.write_text(path.read_text().replace('cpipe_resume: new', 'cpipe_resume: resumed'))
    text = facts.cost(run, tree)
    assert 'missing baseline' in text
    assert not cells(text, '@builder')
    db.close(); worker.close()


def test_atomic_capture_failure_preserves_previous_file(usage, monkeypatch):
    root, tree, run, store, session, row, log = usage
    db = store(); session(db, 'chat'); row(db, 'chat'); facts.cost(run, tree, start=True)
    previous = (run / 'usage-start.json').read_bytes()
    session(db, 'later'); row(db, 'later')
    def fail(*args):
        raise OSError('interrupted replace')
    monkeypatch.setattr(facts.os, 'replace', fail)
    with pytest.raises(OSError, match='interrupted'):
        facts.cost(run, tree, start=True)
    assert (run / 'usage-start.json').read_bytes() == previous
    assert not list(run.glob('.usage-start-*'))
    db.close()


def test_repeated_capture_keeps_new_role_zero_baseline(usage):
    root, tree, run, store, session, row, log = usage
    db = store(); session(db, 'chat'); row(db, 'chat')
    worker = store('builder'); facts.cost(run, tree, start=True)
    session(worker, 'new', started=time.time()); row(worker, 'new', values=(5, 6, 7, 8, 1)); log('new')
    before = cells(facts.cost(run, tree), '@builder')
    facts.cost(run, tree, start=True)
    assert cells(facts.cost(run, tree), '@builder') == before
    db.close(); worker.close()


def test_missing_counter_fields_render_unknown_not_zero(usage):
    root, tree, run, store, session, row, log = usage
    db = store(); session(db, 'chat')
    db.execute('DROP TABLE session_model_usage')
    db.execute('ALTER TABLE sessions DROP COLUMN cache_write_tokens')
    db.execute('ALTER TABLE sessions DROP COLUMN api_call_count'); db.commit()
    facts.cost(run, tree, start=True)
    db.execute('UPDATE sessions SET input_tokens=11'); db.commit()
    text = facts.cost(run, tree)
    assert cells(text, 'this chat')[0][4:] == ['11', '0', '?', '0', '?']
    assert cells(text, '**Total**')[0][4:] == ['11', '0', '?', '0', '?']
    assert 'missing counter fields' in text
    db.close()


def test_existing_coordinator_identity_survives_profile_change(usage, monkeypatch):
    root, tree, run, store, session, row, log = usage
    db = store(); session(db, 'chat'); row(db, 'chat'); facts.cost(run, tree, start=True)
    worker = store('builder'); session(worker, 'chat'); row(worker, 'chat')
    session(worker, 'next'); row(worker, 'next')
    monkeypatch.setenv('HERMES_HOME', str(root / 'profiles' / 'builder'))
    monkeypatch.setenv('HERMES_SESSION_ID', 'next')
    (run / 'chats').write_text('chat\nnext\n')
    facts.cost(run, tree, start=True)
    snapshot = json.loads((run / 'usage-start.json').read_text())
    assert sorted(snapshot['coordinators']) == [['builder', 'next'], ['default', 'chat']]
    db.close(); worker.close()


@pytest.mark.parametrize('field,value', [('coordinators', [7]), ('errors', None), ('captured', 'NaN'),
                                        ('stores', {'default': {'captured': 0, 'sessions': {'chat': {}}}})])
def test_malformed_capture_fields_are_partial(usage, field, value):
    root, tree, run, store, session, row, log = usage
    db = store(); session(db, 'chat'); row(db, 'chat'); facts.cost(run, tree, start=True)
    path = run / 'usage-start.json'; data = json.loads(path.read_text()); data[field] = value
    path.write_text(json.dumps(data)); previous = path.read_bytes()
    assert 'partial' in facts.cost(run, tree).lower()
    assert 'partial' in facts.cost(run, tree, start=True).lower()
    assert path.read_bytes() == previous
    db.close()
