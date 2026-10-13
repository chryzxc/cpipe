"""Coordinator models of every strength send malformed calls. Each unambiguous mistake must still work; anything
else must fail with a message naming the fix, never an opaque error."""
import copy
import json

import pytest

from cpipe import native
from .test_artifact import repo  # noqa: F401
from .test_run_workflow import run_context  # noqa: F401


def send(args):
    return json.loads(native.execute(args, session_id='s1'))


def good(ctx):
    _, token, spec, approach, _, _ = ctx
    return dict(token=token, operation='prepare',
                arguments=dict(spec=copy.deepcopy(spec), approach=copy.deepcopy(approach), expected_revision=0))


def _spec(f):
    return lambda a: (f(a['arguments']['spec']), a)[1]


def _approach(f):
    return lambda a: (f(a['arguments']['approach']), a)[1]


def _no_args(a):
    a.update(a.pop('arguments'))
    return a


def _nested_approach(a):
    a['arguments']['spec']['approach'] = a['arguments'].pop('approach')
    return a


def _strings(a):
    a['arguments'] = json.dumps(a['arguments'])
    return a


def _whole(a):
    return json.dumps(a)


def _checks(spec):
    check = spec['checks'][0]
    check['argv'] = ' '.join(check['argv'])
    check['criteria'] = check['criteria'][0]
    check['timeout'] = check.pop('timeout_seconds')
    del check['obligation']
    spec['checks'] = check


ACCEPTED = {
    'task beside arguments': lambda a: dict(a, task='t1'),
    'task inside arguments, upper case': lambda a: a['arguments'].update(task='T01') or a,
    'empty approach sections left out': _approach(lambda ap: [ap.pop(k) for k in ('decisions', 'risks')]),
    'optional title': _spec(lambda s: s.update(title='Fix doubling')),
    'approach inside spec': _nested_approach,
    'arguments as a JSON string': _strings,
    'whole call as a JSON string': _whole,
    'fields beside, no arguments object': _no_args,
    'operation left out': lambda a: a.pop('operation') and a,
    'operation in capitals': lambda a: dict(a, operation='Prepare'),
    'expected_revision left out': lambda a: a['arguments'].pop('expected_revision') is not None and a,
    'expected_revision as text': lambda a: a['arguments'].update(expected_revision='0') or a,
    'seconds as text': _spec(lambda s: s.update(seconds='300')),
    'seconds out of range': _spec(lambda s: s.update(seconds=99999)),
    'seconds left out': _spec(lambda s: s.pop('seconds')),
    'acceptance as one string': _spec(lambda s: s.update(acceptance='double returns n * 2')),
    'repeated acceptance': _spec(lambda s: s.update(acceptance=['a', 'a', ' '])),
    'non_goals left out': _spec(lambda s: s.pop('non_goals')),
    'forbidden_paths left out': _spec(lambda s: s.pop('forbidden_paths')),
    'write paths with ./ and absolute': _spec(lambda s: s.update(
        write_paths=['./double.py', s['worktree'] + '/test_behavior.py'])),
    'worktree with trailing slash': _spec(lambda s: s.update(worktree=s['worktree'] + '/')),
    'single check, string argv, aliases': _spec(_checks),
    'kind, risk in other words, depth left out': _approach(lambda ap: ap.update(kind='Bugfix', risk='low')
                                                           or ap.pop('depth')),
    'summary with padding': _approach(lambda ap: ap.update(summary='  Return twice the input.\n')),
    'evidence as one string': _approach(lambda ap: ap.update(evidence='double(2) returns 2')),
}


@pytest.mark.parametrize('mistake', ACCEPTED)
def test_unambiguous_mistakes_still_prepare(run_context, mistake):
    out = send(ACCEPTED[mistake](good(run_context)))
    assert 'error' not in out, out
    assert out['draft_revision'] == 1


def test_a_missing_role_comes_from_the_last_delivery(run_context, hermes_home):
    (hermes_home / 'cpipe-deliveries').mkdir(exist_ok=True)
    (hermes_home / 'cpipe-deliveries' / 'roles.json').write_text('{"builder": "builder", "reviewer": "reviewer"}')
    args = good(run_context)
    del args['arguments']['spec']['reviewer']
    assert send(args)['draft_revision'] == 1


NAMED = {
    'approach left out': (lambda a: a['arguments'].pop('approach') and a, 'missing approach'),
    'approach with summary only': (lambda a: a['arguments'].update(approach={'summary': 'x'}) or a,
                                   'approach fields: missing kind'),
    'goal left out': (_spec(lambda s: s.pop('goal')), 'missing goal'),
    'unknown spec field': (_spec(lambda s: s.update(notes='x')), 'unsupported notes'),
    'unknown kind': (_approach(lambda ap: ap.update(kind='weird')), 'kind must be bug, feature or change'),
    'feature without decisions': (_approach(lambda ap: ap.update(kind='feature', depth='focused')),
                                  'need depth detailed with at least one decision'),
    'no steps': (_approach(lambda ap: ap.update(steps=[])), 'steps must list at least one'),
    'path escaping the repo': (_spec(lambda s: s.update(write_paths=['../x.py'])), "'../x.py' must be a path"),
    'unknown operation': (lambda a: dict(a, operation='deploy'), 'operation must be one of'),
    'token left out': (lambda a: a.pop('token') and a, 'token is required'),
    'stale revision': (lambda a: a['arguments'].update(expected_revision=4) or a, 'revision'),
}


@pytest.mark.parametrize('mistake', NAMED)
def test_other_mistakes_name_the_fix(run_context, mistake):
    change, words = NAMED[mistake]
    error = send(change(good(run_context)))['error']
    assert words in error and 'call again' in error, error


WRONG = [None, 5, 'x', {}, [], [5], {'a': 1}, True]


@pytest.mark.parametrize('where', ['spec', 'approach'])
def test_any_wrong_field_fails_with_a_message_never_opaquely(run_context, where):
    """Every field, left out or of every wrong type: it works or names the fix; never a bare exception type."""
    base, opaque = good(run_context), []
    for field in list(base['arguments'][where]) + ['checks.0.argv', 'checks.0.criteria', 'checks.0.timeout_seconds']:
        if field.startswith('checks') and where != 'spec':
            continue
        for value in [...] + WRONG:
            args = copy.deepcopy(base)
            target, name = args['arguments'][where], field
            if field.startswith('checks.'):
                target, name = target['checks'][0], field.split('.')[2]
            if value is ...:
                del target[name]
            else:
                target[name] = value
            out = send(args)
            if 'error' in out:
                if not out['error'].startswith('cpipe operation failed: ValueError ('):
                    opaque.append((field, value, out['error'][:60]))
            else:  # accepted: it must have produced a draft; start again for the next case
                assert out['draft_revision'] >= 1, (field, value, out)
                base['arguments']['expected_revision'] = out['draft_revision']
    assert not opaque, '\n'.join(f'{f} = {v!r}: {e}' for f, v, e in opaque)


def test_later_stages_take_what_models_send(run_context):
    token = run_context[1]
    send(good(run_context))
    held = send(dict(token=token, operation='plan', arguments={'draft_revision': '1', 'note': 'x'}))
    assert held['delivery_id'] and send(dict(token=token, operation='plan', arguments={})) == held
    assert 'is not the current draft' in send(dict(token=token, operation='plan', arguments={'draft_revision': 3}))['error']
    assert send(dict(token=token, operation='status', arguments={'verbose': True}))['delivery_id'] == held['delivery_id']
    assert send(dict(token=token, arguments={}))['delivery_id'] == held['delivery_id']  # no operation: status
    error = send(dict(token=token, operation='build', arguments={}))['error']
    assert 'approval' in error and 'call again' in error  # never approved: build refuses, naming why
