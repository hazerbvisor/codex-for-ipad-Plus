#!/usr/bin/env python3
"""Validate the actual release archive before fakefs import and IPA bundling."""
import argparse
import hashlib
import json
from pathlib import Path
import ssl
import tarfile

PROJECT = Path(__file__).resolve().parents[1]


def verify(archive, codex, runtime):
    with tarfile.open(archive, 'r:gz') as root:
        entries = {}
        for member in root:
            name = member.name.removeprefix('./').lstrip('/')
            if name in entries:
                raise ValueError('Duplicate runtime archive entry')
            entries[name] = member

        def read(path, executable=False):
            member = entries.get(path)
            if not member or not member.isfile() or (executable and member.mode & 0o111 != 0o111):
                raise ValueError(f'Missing or invalid runtime file: {path}')
            return root.extractfile(member).read()

        manifest = json.loads(read('usr/local/share/codexpad/runtime.json'))
        expected = dict(schemaVersion=1, hasAppServer=True, hasCodeModeHost=True, codexRevision=codex['revision'],
                        ishRevision=runtime['revision'], target=codex['target'],
                        alpineRelease=runtime['alpineRelease'])
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise ValueError('Runtime manifest differs from the pinned app')
        server = read('usr/local/libexec/codexpad/codex-app-server', executable=True)
        if server[:6] != b'\x7fELF\x02\x01' or int.from_bytes(server[18:20], 'little') != 183:
            raise ValueError('Runtime server is not little-endian AArch64 ELF')
        checksum = read('usr/local/share/codexpad/codex-app-server.sha256').decode().split()
        if checksum != [hashlib.sha256(server).hexdigest(), '/usr/local/libexec/codexpad/codex-app-server']:
            raise ValueError('Runtime server checksum differs')
        provenance_path = 'usr/local/share/codexpad/codex-app-server.provenance.json'
        if provenance_path in entries:
            provenance = json.loads(read(provenance_path))
            expected = dict(source='official-release', repository=codex['repository'],
                            revision=codex['revision'], target=codex['target'],
                            tag=codex['release']['tag'], archiveSha256=codex['release']['sha256'],
                            binarySha256=checksum[0])
            if any(provenance.get(key) != value for key, value in expected.items()):
                raise ValueError('Runtime official-release provenance differs')
        helper = read('usr/local/libexec/codexpad/codex-code-mode-host', executable=True)
        helper_hash = hashlib.sha256(helper).hexdigest()
        if helper_hash != codex['codeModeHostRelease']['binarySha256']:
            raise ValueError('Runtime Code Mode helper differs from the pinned official binary')
        if helper[:6] != b'\x7fELF\x02\x01' or int.from_bytes(helper[18:20], 'little') != 183:
            raise ValueError('Runtime Code Mode helper is not AArch64 ELF')
        helper_checksum = read('usr/local/share/codexpad/codex-code-mode-host.sha256').decode().split()
        if helper_checksum != [helper_hash, '/usr/local/libexec/codexpad/codex-code-mode-host']:
            raise ValueError('Runtime Code Mode helper checksum differs')
        helper_provenance = json.loads(read('usr/local/share/codexpad/codex-code-mode-host.provenance.json'))
        expected = dict(source='official-release', repository=codex['repository'],
                        revision=codex['revision'], target=codex['target'],
                        tag=codex['release']['tag'], archiveSha256=codex['codeModeHostRelease']['sha256'],
                        binarySha256=helper_hash)
        if any(helper_provenance.get(key) != value for key, value in expected.items()):
            raise ValueError('Runtime Code Mode helper provenance differs')
        for path in ('sbin/openrc', 'sbin/rc-service', 'usr/local/libexec/codexpad/boot',
                     'usr/local/libexec/codexpad/diagnose'):
            read(path, executable=True)
        read('etc/profile.d/codexpad.sh')
        service = read('etc/init.d/codexpad', executable=True).decode()
        if ('command="/usr/local/libexec/codexpad/codex-app-server"' not in service or
                'command_args="--listen ws://127.0.0.1:4500"' not in service):
            raise ValueError('Runtime service does not launch the official loopback server')
        runlevel = entries.get('etc/runlevels/default/codexpad')
        if not runlevel or not runlevel.issym() or runlevel.linkname != '/etc/init.d/codexpad':
            raise ValueError('Runtime service is not enabled in the default runlevel')
        if '::sysinit:/usr/local/libexec/codexpad/boot' not in read('etc/inittab').decode().splitlines():
            raise ValueError('Runtime init does not run the boot adapter')
        certificates = read('etc/ssl/certs/ca-certificates.crt').decode('ascii')
        default_certificates = entries.get('etc/ssl/cert.pem')
        if (not default_certificates or not default_certificates.issym() or
                default_certificates.linkname not in ('certs/ca-certificates.crt',
                                                       '/etc/ssl/certs/ca-certificates.crt')):
            raise ValueError('Runtime default certificate path does not resolve to the CA bundle')
        trust = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        trust.load_verify_locations(cadata=certificates)
        if trust.cert_store_stats()['x509_ca'] == 0:
            raise ValueError('Runtime certificate bundle contains no CA certificates')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    verify(args.archive, json.loads((PROJECT / 'Dependencies/upstreams.json').read_text())['codex'],
           json.loads((PROJECT / 'Dependencies/arm64-runtime.json').read_text()))
    print('PASS: runtime server and Code Mode helper, pins, checksums, service, boot and CA bundle')
