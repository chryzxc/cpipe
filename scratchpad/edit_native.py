from pathlib import Path
p = Path('cpipe/native.py')
s = p.read_text()
old = '''                from .delivery.approach import execute as execute_run
                return json.dumps(execute_run(service, run_record, _intent_key(token), op, payload,'''
new = '''                run_key = _intent_key(token)
                if 'task' in payload:  # one /cp-run, several independent deliveries: one sub-receipt each
                    payload = dict(payload)
                    task = payload.pop('task')
                    if type(task) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,31}', task):
                        raise ValueError('task must be a short lowercase label such as t2')
                    run_key += '-' + task
                    row = db.execute('SELECT payload FROM intent WHERE id=?', (run_key,)).fetchone()
                    if row is None:
                        run_record = {k: run_record[k] for k in (
                            'kind', 'operation', 'session_id', 'session_key', 'profile_id', 'expires')}
                        run_record.update(state='pending', result=None)
                        if op != 'prepare' or run_record['expires'] <= time.time():
                            raise ValueError('start a task with prepare')
                        db.execute('INSERT INTO intent(id,payload,acknowledged) VALUES (?,?,1)', (run_key, encoded(run_record)))
                    else:
                        run_record = json.loads(row[0])
                from .delivery.approach import execute as execute_run
                return json.dumps(execute_run(service, run_record, run_key, op, payload,'''
assert old in s
s = s.replace(old, new)
p.write_text(s)
