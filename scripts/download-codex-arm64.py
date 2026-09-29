#!/usr/bin/env python3
"""Stage the checksum-pinned official Linux AArch64 musl app-server."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

PROJECT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--archive', type=Path, help='Verify a previously downloaded archive')
    args = parser.parse_args()
    config = json.loads((PROJECT / 'Dependencies/upstreams.json').read_text())['codex']
    release = config['release']
    asset = release['asset']
    tag = release['tag']
    expected = release['sha256']
    if (config['repository'] != 'openai/codex'
            or config['target'] != 'aarch64-unknown-linux-musl'
            or asset != 'codex-app-server-aarch64-unknown-linux-musl.tar.gz'
            or not re.fullmatch(r'rust-v[0-9A-Za-z.\-]+', tag)
            or not re.fullmatch(r'[0-9a-f]{64}', expected)):
        raise ValueError('Invalid official ARM64 release pin')
    url = f'https://github.com/openai/codex/releases/download/{tag}/{asset}'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Stage beside the destination for an atomic rename; never run guest ELF on macOS.
    with tempfile.TemporaryDirectory(dir=args.output.parent) as temporary:
        work = Path(temporary)
        archive = args.archive
        cached = args.cache_dir / f'{expected}.tar.gz' if args.cache_dir else None
        if archive is None:
            if cached and cached.is_file() and sha256(cached) == expected:
                archive = cached
            else:
                archive = work / 'release.tar.gz'
                subprocess.run(['curl', '--proto', '=https', '--proto-redir', '=https',
                                '--tlsv1.2', '--fail', '--location', '--retry', '3',
                                '--silent', '--show-error', url, '--output', str(archive)], check=True)
        if sha256(archive) != expected:
            raise ValueError('Official Codex archive SHA-256 mismatch')
        binary = work / 'codex-app-server'
        with tarfile.open(archive, 'r:gz') as bundle:
            members = bundle.getmembers()
            if (len(members) != 1 or members[0].name != asset.removesuffix('.tar.gz')
                    or not members[0].isfile()):
                raise ValueError('Unexpected official Codex archive contents')
            with bundle.extractfile(members[0]) as source, binary.open('wb') as destination:
                shutil.copyfileobj(source, destination)
        subprocess.run(['python3', str(PROJECT / 'scripts/check-arm64-elf.py'),
                        '--static', str(binary)], check=True)
        binary.chmod(0o755)
        provenance = {'schemaVersion': 1, 'source': 'official-release',
                      'repository': config['repository'], 'revision': config['revision'],
                      'tag': tag, 'target': config['target'], 'url': url,
                      'archiveSha256': expected, 'binarySha256': sha256(binary)}
        if cached and archive != cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            staged_cache = work / 'cache.tar.gz'
            shutil.copyfile(archive, staged_cache)
            # Cache can reside on another filesystem.
            with tempfile.NamedTemporaryFile(dir=cached.parent, delete=False) as stream:
                cache_temp = Path(stream.name)
            try:
                shutil.copyfile(staged_cache, cache_temp)
                os.replace(cache_temp, cached)
            finally:
                cache_temp.unlink(missing_ok=True)
        os.replace(binary, args.output)
        manifest = args.output.with_name(args.output.name + '.provenance.json')
        manifest.write_text(json.dumps(provenance, indent=2) + '\n')
        print(f'Verified official {tag}: static AArch64 app-server -> {args.output}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError, tarfile.TarError) as error:
        raise SystemExit(str(error))
