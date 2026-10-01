import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

project = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('import_check', project / 'scripts/verify-imported-runtime.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class ImportedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.paths = ('/usr/local/libexec/codexpad/codex-app-server', '/etc/init.d/codexpad',
                      '/sbin/openrc', '/sbin/rc-service', '/etc/profile.d/codexpad.sh',
                      '/usr/local/share/codexpad/runtime.json')
        with sqlite3.connect(self.root / 'meta.db') as db:
            db.executescript('create table paths(path blob,inode integer); create table stats(inode integer,stat blob);')
            for inode, path in enumerate(self.paths):
                db.execute('insert into paths values (?,?)', (path.encode(), inode))
                db.execute('insert into stats values (?,?)', (inode, b'stat'))
                file = self.root / 'data' / path.lstrip('/')
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b'fixture')
        self.manifest = self.root / 'data/usr/local/share/codexpad/runtime.json'
        self.manifest.write_text(json.dumps({'hasAppServer': True, 'target': 'aarch64-unknown-linux-musl', 'codexRevision': 'abc'}))
        self.binary = self.root / 'data/usr/local/libexec/codexpad/codex-app-server'
        self.binary.write_bytes(b'\x7fELF\x02' + bytes(13) + (183).to_bytes(2, 'little'))

    def test_valid_import(self):
        check.verify(self.root, 'abc')

    def test_disk_file_without_guest_metadata_is_rejected(self):
        with sqlite3.connect(self.root / 'meta.db') as db:
            db.execute('delete from paths where path=?', (b'/etc/init.d/codexpad',))
        with self.assertRaisesRegex(ValueError, 'missing /etc/init.d/codexpad'):
            check.verify(self.root, 'abc')

    def test_missing_server_is_rejected(self):
        self.binary.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            check.verify(self.root, 'abc')

    def test_wrong_revision_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'manifest differs'):
            check.verify(self.root, 'other')

    def test_wrong_architecture_is_rejected(self):
        self.binary.write_bytes(b'\x7fELF\x02' + bytes(13) + (62).to_bytes(2, 'little'))
        with self.assertRaisesRegex(ValueError, 'not AArch64'):
            check.verify(self.root, 'abc')


if __name__ == '__main__':
    unittest.main()
