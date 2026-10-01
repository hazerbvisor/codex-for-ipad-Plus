#!/usr/bin/env python3
"""Require one bundled runtime matching the verified build input."""
import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT = 'Payload/CodexPad.app/root.tar.gz'
LEGACY = 'Payload/CodexPad.app/codexpad-runtime.tar.gz'


def digest(stream):
    result = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        result.update(block)
    return result.digest()


def verify(ipa, runtime):
    with zipfile.ZipFile(ipa) as archive:
        names = archive.namelist()
        if names.count(ROOT) != 1 or LEGACY in names:
            raise ValueError('IPA must bundle exactly one runtime as root.tar.gz')
        with archive.open(ROOT) as bundled, Path(runtime).open('rb') as expected:
            if digest(bundled) != digest(expected):
                raise ValueError('Bundled runtime differs from the verified build input')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ipa', type=Path)
    parser.add_argument('runtime', type=Path)
    args = parser.parse_args()
    verify(args.ipa, args.runtime)
    print('PASS: IPA contains one runtime archive matching the verified build input')
