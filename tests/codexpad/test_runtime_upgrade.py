"""Exercise the actual pre-boot C installer and fakefs metadata preservation."""
import ctypes
import hashlib
import io
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
HOST = '/usr/local/libexec/codexpad/codex-code-mode-host'


class RuntimeUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        work = Path(cls.build.name)
        wrapper = work / 'upgrade.c'
        wrapper.write_text('#include "' + str(PROJECT / 'scripts/CodexPadRuntimeUpgrade.c') + '"\n'
                           'int install_host(const char *a,const char *r,const char *h) '
                           '{ return CodexPadInstallCodeModeHost(a,r,h); }\n'
                           'int has_host(const char *r,const char *h) '
                           '{ return CodexPadRootHasCodeModeHost(r,h); }\n')
        extra = ['-lcrypto']
        link = '-shared'
        if sys.platform == 'darwin':
            prefix = subprocess.check_output(['brew', '--prefix', 'libarchive'], text=True).strip()
            extra = ['-I' + prefix + '/include', '-L' + prefix + '/lib']
            link = '-dynamiclib'
        library = work / 'upgrade.so'
        subprocess.run([shutil.which('cc') or 'cc', '-std=gnu11', '-Wall', '-Werror',
                        '-fPIC', link, '-DGUEST_ARM64=1', '-I' + str(PROJECT / 'upstream/ios-linuxkit'),
                        *shlex.split(os.environ.get('CPPFLAGS', '')), str(wrapper), '-o', str(library),
                        *shlex.split(os.environ.get('LDFLAGS', '')), '-larchive', '-lsqlite3', *extra], check=True)
        cls.native = ctypes.CDLL(str(library))
        cls.native.install_host.argtypes = [ctypes.c_char_p] * 3
        cls.native.has_host.argtypes = [ctypes.c_char_p] * 2

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name) / 'guest'
        parent = self.root / 'data/usr/local/libexec/codexpad'
        parent.mkdir(parents=True)
        with sqlite3.connect(self.root / 'meta.db') as db:
            db.executescript('CREATE TABLE stats(inode INTEGER PRIMARY KEY,stat BLOB);'
                             'CREATE TABLE paths(path BLOB PRIMARY KEY,inode INTEGER REFERENCES stats(inode));')
            for path in ('/usr', '/usr/local', '/usr/local/libexec', '/usr/local/libexec/codexpad'):
                inode = db.execute('INSERT INTO stats(stat) VALUES(?)',
                                   (struct.pack('<4I', stat.S_IFDIR | 0o755, 0, 0, 0),)).lastrowid
                db.execute('INSERT INTO paths VALUES(?,?)', (path.encode(), inode))
        # Fixed private sentinels remain on disk; their contents never enter logs.
        self.private = ('root/.codex/auth.json', 'root/.codex/config.toml', 'root/workspace/project.txt')
        for path in self.private:
            file = self.root / 'data' / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'private-preservation-fixture')
        binary = bytearray(64)
        binary[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', binary, 18, 183)
        self.binary = bytes(binary)
        self.expected = hashlib.sha256(self.binary).hexdigest().encode()
        self.archive = Path(self.work.name) / 'root.tar.gz'

    def package(self, members=None):
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, data in members or [(HOST.lstrip('/'), self.binary)]:
                entry = tarfile.TarInfo('./' + name)
                entry.size, entry.mode = len(data), 0o755
                archive.addfile(entry, io.BytesIO(data))

    def install(self):
        return self.native.install_host(str(self.archive).encode(), str(self.root).encode(), self.expected)

    def present(self):
        return self.native.has_host(str(self.root).encode(), self.expected)

    def assert_private_preserved(self):
        for path in self.private:
            self.assertEqual((self.root / 'data' / path).read_bytes(), b'private-preservation-fixture')
        self.assertEqual(list((self.root / 'data/usr/local/libexec/codexpad').glob('.codexpad-helper-*')), [])

    def test_install_preserves_data_and_registers_guest_metadata(self):
        self.package([('root/.codex/auth.json', b'must-never-install'), (HOST.lstrip('/'), self.binary)])
        self.assertEqual(self.present(), 0)
        with sqlite3.connect(self.root / 'meta.db') as db:
            before = db.execute('SELECT * FROM paths').fetchall()
        self.assertEqual(self.install(), 0)
        self.assertEqual(self.present(), 1)
        self.assertEqual((self.root / 'data' / HOST.lstrip('/')).read_bytes(), self.binary)
        with sqlite3.connect(self.root / 'meta.db') as db:
            self.assertEqual(db.execute('SELECT * FROM paths WHERE path<>?', (HOST.encode(),)).fetchall(), before)
            metadata = db.execute('SELECT stat FROM paths JOIN stats USING(inode) WHERE path=?',
                                  (HOST.encode(),)).fetchone()[0]
        self.assertEqual(struct.unpack('<4I', metadata), (stat.S_IFREG | 0o755, 0, 0, 0))
        self.assert_private_preserved()
        snapshot = (self.root / 'meta.db').read_bytes()
        self.archive.unlink()  # Already installed: no import/download/DB rewrite.
        self.assertEqual(self.install(), 0)
        self.assertEqual((self.root / 'meta.db').read_bytes(), snapshot)

    def test_file_without_guest_metadata_retries_install(self):
        self.package()
        (self.root / 'data' / HOST.lstrip('/')).write_bytes(self.binary)
        self.assertEqual(self.present(), 0)
        self.assertEqual(self.install(), 0)
        self.assertEqual(self.present(), 1)
        self.assert_private_preserved()

    def test_corrupt_installed_helper_is_repaired(self):
        self.package()
        self.assertEqual(self.install(), 0)
        helper = self.root / 'data' / HOST.lstrip('/')
        helper.write_bytes(self.binary + b'corruption')
        self.assertEqual(self.present(), 0)
        self.assertEqual(self.install(), 0)
        self.assertEqual(self.present(), 1)
        self.assertEqual(helper.read_bytes(), self.binary)
        self.assert_private_preserved()

    def test_missing_duplicate_and_wrong_digest_never_register_helper(self):
        for members in ([('unrelated', b'fixture')], [(HOST.lstrip('/'), self.binary)] * 2,
                        [(HOST.lstrip('/'), self.binary + b'corrupt')]):
            with self.subTest(members=len(members)):
                self.package(members)
                self.assertNotEqual(self.install(), 0)
                self.assertEqual(self.present(), 0)
                self.assert_private_preserved()

    def test_symlinked_parent_is_rejected(self):
        self.package()
        parent = self.root / 'data/usr/local/libexec/codexpad'
        parent.rmdir()
        outside = Path(self.work.name) / 'outside'
        outside.mkdir()
        parent.symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.install(), 0)
        self.assertEqual(list(outside.iterdir()), [])
        self.assert_private_preserved()


if __name__ == '__main__':
    unittest.main()
