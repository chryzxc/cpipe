#!/usr/bin/env python3
"""Merge cron job definitions into the live jobs.json, by name, owned fields only.

Jobs this script writes are stamped with owner=OWNER. On every merge, owned jobs
whose name no longer appears in the definitions are pruned. Jobs without the
owner marker (foreign jobs) are never added, updated, or pruned.
"""
import json
import sys

OWNER = 'software-delivery'
OWNED = {'prompt', 'script', 'no_agent', 'monitor_script', 'model', 'provider',
         'schedule', 'enabled', 'deliver', 'origin', 'workdir', 'created_at'}


def main(defs_path, live_path):
    defs = json.load(open(defs_path))
    live = json.load(open(live_path))
    jobs = live if isinstance(live, list) else live.setdefault('jobs', live.get('jobs', []))
    existing = {j.get('name'): j for j in jobs}
    def_names = {d['name'] for d in defs}
    for d in defs:
        j = existing.get(d['name'])
        if j is None:
            entry = dict(d)
            entry['owner'] = OWNER
            jobs.append(entry)
            print(f"   added: {d['name']}")
        else:
            j['owner'] = OWNER
            changed = [k for k in OWNED if k in d and j.get(k) != d[k]]
            for k in changed:
                j[k] = d[k]
            print(f"   updated: {d['name']}" + (f" ({', '.join(changed)})" if changed else " (in sync)"))
    pruned = [j.get('name') for j in jobs
              if j.get('owner') == OWNER and j.get('name') not in def_names]
    if pruned:
        jobs[:] = [j for j in jobs if not (j.get('owner') == OWNER and j.get('name') not in def_names)]
        for name in pruned:
            print(f"   pruned: {name}")
    json.dump(live, open(live_path, 'w'), indent=2, ensure_ascii=False)
    return pruned


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
