#!/usr/bin/env python3
"""Developer-only checksum generation; Recovery does not need Python."""
from pathlib import Path
import hashlib
import re

ROOT = Path(__file__).resolve().parents[1]


def release_files():
    text = (ROOT / 'SELF_TEST.sh').read_text()
    match = re.search(r"REQUIRED_FILES='([^']*)'", text)
    if not match:
        raise ValueError('SELF_TEST.sh has no release file list')
    names = match.group(1).splitlines()
    if len(names) != len(set(names)):
        raise ValueError('Duplicate release file')
    for name in names:
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or not name:
            raise ValueError('Invalid release path')
        if not (ROOT / path).is_file() or (ROOT / path).is_symlink():
            raise ValueError('Missing or symlinked release file: ' + name)
    return names


if __name__ == '__main__':
    names = release_files()
    output = ''.join(hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                     + '  ' + name + '\n' for name in names)
    (ROOT / 'SHA256SUMS').write_text(output)
    print('checksums_written=' + str(len(names)))
