#!/usr/bin/env python3
"""Check only publishable source files for accidental local/private artifacts."""
from pathlib import Path
import re
from update_checksums import ROOT, release_files


def main():
    allowed_uuids = {'11111111-2222-4333-8444-555555555555',
                     'AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE'}
    problems = []
    for name in release_files() + ['SHA256SUMS']:
        raw = (ROOT / name).read_bytes()
        text = raw.decode('utf-8', errors='ignore')
        home_prefix = '/' + 'Users' + '/'
        for match in re.finditer(re.escape(home_prefix) + r'([^/\s"\x27]+)', text):
            if match.group(1) not in {'Shared'}:
                problems.append(name + ': personal home path')
        if '/' + 'var/folders/' in text:
            problems.append(name + ': private temporary path')
        if re.search(r'gh[pousr]_[A-Za-z0-9]{20,}', text):
            problems.append(name + ': possible GitHub credential')
        for value in re.findall(r'\b[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\b', text):
            if value.upper() not in allowed_uuids:
                problems.append(name + ': non-fixture UUID')
    if problems:
        raise SystemExit('\n'.join(sorted(set(problems))))
    print('public_source_scan=PASS')


if __name__ == '__main__':
    main()
