#!/usr/bin/env python3
"""Assert live config.yaml matches workflow/config.assertions.yaml. Exit 1 on drift."""
import re
import sys


def flat_section(path, section):
    found, current = {}, None
    for line in open(path):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not line[:1].isspace() and line.rstrip().endswith(':'):
            current = line.strip()[:-1]
            continue
        m = re.match(r'\s*([\w-]+):\s*(\S+)', line)
        if m and current == section:
            found[m.group(1)] = m.group(2)
    return found


def main(assertions_path, config_path):
    expected = flat_section(assertions_path, 'kanban')
    text = open(config_path).read()
    drift = []
    for key, want in expected.items():
        m = re.search(rf'^\s*{re.escape(key)}:\s*(\d+)\s*$', text, re.M)
        got = int(m.group(1)) if m else None
        if got != int(want):
            drift.append(f'kanban.{key}: expected {want}, found {got}')
    if drift:
        print('CONFIG DRIFT:\n' + '\n'.join('  - ' + d for d in drift))
        sys.exit(1)
    print('config assertions OK')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
