import hashlib
import importlib.util
import io
import json
from pathlib import Path
import ssl
import tarfile
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('archive_check', PROJECT / 'scripts/verify-runtime-archive.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class RuntimeArchiveTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.archive = Path(work.name) / 'root.tar.gz'
        self.codex = json.loads((PROJECT / 'Dependencies/upstreams.json').read_text())['codex']
        self.runtime = json.loads((PROJECT / 'Dependencies/arm64-runtime.json').read_text())
        binary = b'\x7fELF\x02\x01' + bytes(12) + (183).to_bytes(2, 'little')
        manifest = dict(schemaVersion=1, hasAppServer=True, hasCodeModeHost=True, codexRevision=self.codex['revision'],
                        ishRevision=self.runtime['revision'], target=self.codex['target'],
                        alpineRelease=self.runtime['alpineRelease'])
        # Public ISRG Root X1 certificate (letsencrypt.org/certs/isrgrootx1.pem).
        # A fixed fixture also works before macOS Python's CA store is configured.
        certificate = (PROJECT / 'tests/codexpad/fixtures/isrg-root-x1.crt').read_bytes()
        self.files = {
            'usr/local/libexec/codexpad/codex-app-server': binary,
            'usr/local/share/codexpad/codex-app-server.sha256':
                (hashlib.sha256(binary).hexdigest() + '  /usr/local/libexec/codexpad/codex-app-server\n').encode(),
            'usr/local/share/codexpad/runtime.json': json.dumps(manifest).encode(),
            'sbin/openrc': b'fixture', 'sbin/rc-service': b'fixture',
            'usr/local/libexec/codexpad/boot': b'fixture',
            'usr/local/libexec/codexpad/diagnose': b'fixture',
            'etc/profile.d/codexpad.sh': b'fixture',
            'etc/init.d/codexpad': (PROJECT / 'runtime/etc/init.d/codexpad').read_bytes(),
            'etc/inittab': b'::sysinit:/usr/local/libexec/codexpad/boot\n',
            'etc/ssl/certs/ca-certificates.crt': certificate,
        }
        self.runlevel = '/etc/init.d/codexpad'
        helper_hash = hashlib.sha256(binary).hexdigest()
        self.codex['codeModeHostRelease']['binarySha256'] = helper_hash
        self.files['usr/local/libexec/codexpad/codex-code-mode-host'] = binary
        self.files['usr/local/share/codexpad/codex-code-mode-host.sha256'] = (
            helper_hash + '  /usr/local/libexec/codexpad/codex-code-mode-host\n').encode()
        self.files['usr/local/share/codexpad/codex-code-mode-host.provenance.json'] = json.dumps(dict(
            source='official-release', repository=self.codex['repository'], revision=self.codex['revision'],
            target=self.codex['target'], tag=self.codex['release']['tag'],
            archiveSha256=self.codex['codeModeHostRelease']['sha256'], binarySha256=helper_hash)).encode()

    def package(self):
        with tarfile.open(self.archive, 'w:gz') as root:
            for name, data in self.files.items():
                member = tarfile.TarInfo('./' + name)
                member.size, member.mode = len(data), 0o755
                root.addfile(member, io.BytesIO(data))
            if self.runlevel:
                member = tarfile.TarInfo('./etc/runlevels/default/codexpad')
                member.type, member.linkname = tarfile.SYMTYPE, self.runlevel
                root.addfile(member)
            member = tarfile.TarInfo('./etc/ssl/cert.pem')
            member.type, member.linkname = tarfile.SYMTYPE, 'certs/ca-certificates.crt'
            root.addfile(member)

    def verify(self):
        self.package()
        check.verify(self.archive, self.codex, self.runtime)

    def test_complete_archive(self):
        self.verify()

    def test_missing_helper_and_metadata_are_rejected(self):
        for path in ('usr/local/libexec/codexpad/codex-code-mode-host',
                     'usr/local/share/codexpad/codex-code-mode-host.sha256',
                     'usr/local/share/codexpad/codex-code-mode-host.provenance.json'):
            with self.subTest(path=path):
                data = self.files.pop(path)
                with self.assertRaises(ValueError): self.verify()
                self.files[path] = data

    def test_corrupt_helper_is_rejected(self):
        self.files['usr/local/libexec/codexpad/codex-code-mode-host'] += b'corruption'
        with self.assertRaisesRegex(ValueError, 'pinned official binary'): self.verify()

    def test_helper_from_different_release_is_rejected(self):
        path = 'usr/local/share/codexpad/codex-code-mode-host.provenance.json'
        provenance = json.loads(self.files[path]); provenance['tag'] = 'rust-v0.1.0'
        self.files[path] = json.dumps(provenance).encode()
        with self.assertRaisesRegex(ValueError, 'helper provenance'): self.verify()

    def test_corrupt_server(self):
        self.files['usr/local/libexec/codexpad/codex-app-server'] += b'corrupted'
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.verify()

    def test_wrong_emulator_pin(self):
        path = 'usr/local/share/codexpad/runtime.json'
        manifest = json.loads(self.files[path])
        manifest['ishRevision'] = 'stale'
        self.files[path] = json.dumps(manifest).encode()
        with self.assertRaisesRegex(ValueError, 'manifest'):
            self.verify()

    def test_disabled_service(self):
        self.runlevel = None
        with self.assertRaisesRegex(ValueError, 'runlevel'):
            self.verify()

    def test_service_points_at_wrong_server(self):
        path = 'etc/init.d/codexpad'
        self.files[path] = self.files[path].replace(b'127.0.0.1:4500', b'0.0.0.0:4500')
        with self.assertRaisesRegex(ValueError, 'loopback'):
            self.verify()

    def test_missing_certificates(self):
        del self.files['etc/ssl/certs/ca-certificates.crt']
        with self.assertRaisesRegex(ValueError, 'ca-certificates'):
            self.verify()

    def test_invalid_certificates(self):
        self.files['etc/ssl/certs/ca-certificates.crt'] = b'not a certificate'
        with self.assertRaises(ssl.SSLError):
            self.verify()

    def test_stale_official_release_provenance(self):
        self.files['usr/local/share/codexpad/codex-app-server.provenance.json'] = b'{"revision":"stale"}'
        with self.assertRaisesRegex(ValueError, 'provenance'):
            self.verify()


if __name__ == '__main__':
    unittest.main()
