#!/usr/bin/env python3
"""Merge cron job definitions into the live jobs.json, by name, owned fields only."""
import json
import sys

OWNED = {'prompt', 'script', 'no_agent', 'monitor_script', 'model', 'provider',
         'schedule', 'enabled', 'deliver'}


def main(defs_path, live_path):
    defs = json.load(open(defs_path))
    live = json.load(open(live_path))
    jobs = live if isinstance(live, list) else live.setdefault('jobs', live.get('jobs', []))
    existing = {j.get('name'): j for j in jobs}
    for d in defs:
        j = existing.get(d['name'])
        if j is None:
            jobs.append(dict(d))
            print(f"   added: {d['name']}")
        else:
            changed = [k for k in OWNED if k in d and j.get(k) != d[k]]
            for k in changed:
                j[k] = d[k]
            print(f"   updated: {d['name']}" + (f" ({', '.join(changed)})" if changed else " (in sync)"))
    json.dump(live, open(live_path, 'w'), indent=2, ensure_ascii=False)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
