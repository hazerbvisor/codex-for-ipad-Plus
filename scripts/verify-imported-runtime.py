#!/usr/bin/env python3
"""Import the release root with the pinned iSH importer and check guest paths."""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile


def verify(root, revision):
    required = ('/usr/local/libexec/codexpad/codex-app-server', '/etc/init.d/codexpad',
                '/usr/local/libexec/codexpad/codex-code-mode-host',
                '/sbin/openrc', '/sbin/rc-service', '/etc/profile.d/codexpad.sh',
                '/usr/local/share/codexpad/runtime.json')
    with sqlite3.connect(root / 'meta.db') as db:
        for path in required:
            entry = db.execute('select stat from paths join stats using (inode) where path=?',
                               (path.encode(),)).fetchone()
            if entry is None or not (root / 'data' / path.lstrip('/')).is_file():
                raise ValueError(f'Imported guest is missing {path}')
    manifest = json.loads((root / 'data/usr/local/share/codexpad/runtime.json').read_text())
    if not (manifest.get('hasAppServer') is True and manifest.get('hasCodeModeHost') is True and
            manifest.get('target') == 'aarch64-unknown-linux-musl' and
            manifest.get('codexRevision') == revision):
        raise ValueError('Imported runtime manifest differs from the app')
    for component in ('codex-app-server', 'codex-code-mode-host'):
        binary = root / 'data/usr/local/libexec/codexpad' / component
        with binary.open('rb') as stream:
            header = stream.read(20)
        if header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != 183:
            raise ValueError(f'Imported {component} is not AArch64 ELF')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    source = project / 'upstream/ios-linuxkit'
    revision = json.loads((project / 'Dependencies/upstreams.json').read_text())['codex']['revision']
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        harness = work / 'import.c'
        harness.write_text('#include <stdio.h>\n#include "tools/fakefs.h"\n'
            'int main(int argc, char **argv) { if(argc!=3) return 2; '
            'struct fakefsify_error e; if(fakefs_import(argv[1],argv[2],&e,(struct progress){0})) return 0; '
            'fprintf(stderr,"Import failed: %s (line %d)\\n",e.message,e.line); return 1; }\n')
        prefix = subprocess.check_output(['brew', '--prefix', 'libarchive'], text=True).strip()
        importer = work / 'import-runtime'
        subprocess.run(['xcrun', '--sdk', 'macosx', 'clang', '-std=gnu11', '-DGUEST_ARM64=1',
            '-I' + str(source), '-I' + prefix + '/include', '-L' + prefix + '/lib',
            str(harness), str(source / 'tools/fakefs.c'), str(source / 'util/fchdir.c'),
            '-larchive', '-lsqlite3', '-lpthread', '-o', str(importer)], check=True)
        imported = work / 'guest'
        subprocess.run([str(importer), str(args.archive.resolve()), str(imported)], check=True)
        verify(imported, revision)
    print('PASS: actual iSH import contains Codex server/helper, OpenRC and matching runtime manifest')


if __name__ == '__main__':
    main()
