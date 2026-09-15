#!/usr/bin/env python3
"""Build a fixed-layout release without local sessions, logs or git metadata."""
from pathlib import Path
import gzip
import hashlib
import io
import re
import subprocess
import tarfile
from update_checksums import ROOT, release_files


def main():
    names = release_files()
    version = (ROOT / 'VERSION').read_text().strip()
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('VERSION must be vMAJOR.MINOR.PATCH')
    subprocess.run(['/bin/sh', str(ROOT / 'SELF_TEST.sh')], check=True)
    out = ROOT / 'dist'
    out.mkdir(exist_ok=True)
    archive = out / ('macos-depnag-toolkit-' + version + '.tar.gz')
    top = 'macos-depnag-toolkit'
    # Fixed metadata and gzip timestamp keep archive bytes deterministic.
    with archive.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode='w', format=tarfile.USTAR_FORMAT) as tar:
                parents = {top}
                for name in names:
                    parents.update(str(Path(top) / p) for p in Path(name).parents if str(p) != '.')
                for directory in sorted(parents):
                    item = tarfile.TarInfo(directory + '/')
                    item.type = tarfile.DIRTYPE
                    item.mode = 0o755
                    item.mtime = 0
                    tar.addfile(item)
                for name in names + ['SHA256SUMS']:
                    source = ROOT / name
                    data = source.read_bytes()
                    item = tarfile.TarInfo(top + '/' + name)
                    item.mode = source.stat().st_mode & 0o777
                    item.size = len(data)
                    item.mtime = 0
                    tar.addfile(item, io.BytesIO(data))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + '.sha256').write_text(digest + '  ' + archive.name + '\n')
    print('archive=' + str(archive))
    print('archive_sha256=' + digest)
    print('archive_files=' + str(len(names) + 1))


if __name__ == '__main__':
    main()
