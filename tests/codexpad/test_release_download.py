"""Offline checks for release identity, archive safety and architecture rejection."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('download', PROJECT / 'scripts/download-codex-arm64.py')
download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download)


class ReleaseDownloadTests(unittest.TestCase):
    def run_fixture(self, *, member=None, machine=183, bad_hash=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'Dependencies').mkdir()
            (root / 'scripts').mkdir()
            (root / 'scripts/check-arm64-elf.py').write_bytes(
                (PROJECT / 'scripts/check-arm64-elf.py').read_bytes())
            binary = bytearray(64)
            binary[:6] = b'\x7fELF\x02\x01'
            struct.pack_into('<H', binary, 18, machine)
            struct.pack_into('<H', binary, 54, 56)
            asset = 'codex-app-server-aarch64-unknown-linux-musl.tar.gz'
            archive = root / 'archive.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                entry = tarfile.TarInfo(member or asset.removesuffix('.tar.gz'))
                entry.size = len(binary)
                bundle.addfile(entry, io.BytesIO(binary))
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            config = {'codex': {'repository': 'openai/codex', 'revision': 'fixture',
                               'target': 'aarch64-unknown-linux-musl',
                               'release': {'tag': 'rust-v0.155.0-alpha.4', 'asset': asset,
                                           'sha256': '0' * 64 if bad_hash else digest}}}
            (root / 'Dependencies/upstreams.json').write_text(json.dumps(config))
            output = root / 'codex-app-server'
            output.write_bytes(b'previous verified output')
            argv = ['download', '--archive', str(archive), '--output', str(output),
                    '--cache-dir', str(root / 'cache')]
            with patch.object(download, 'PROJECT', root), patch.object(sys, 'argv', argv):
                try:
                    download.main()
                except (ValueError, subprocess.CalledProcessError):
                    self.assertEqual(output.read_bytes(), b'previous verified output')
                    self.assertFalse((root / 'codex-app-server.provenance.json').exists())
                    raise
            self.assertEqual(output.read_bytes(), bytes(binary))
            self.assertTrue(output.stat().st_mode & 0o111)
            manifest = json.loads((root / 'codex-app-server.provenance.json').read_text())
            self.assertEqual(manifest['binarySha256'], hashlib.sha256(binary).hexdigest())
            # Reuse the verified cache with the original archive removed; no network.
            archive.unlink()
            argv = ['download', '--output', str(output), '--cache-dir', str(root / 'cache')]
            with patch.object(download, 'PROJECT', root), patch.object(sys, 'argv', argv):
                download.main()
            self.assertEqual(output.read_bytes(), bytes(binary))

    def test_verified_artifact_and_offline_cache(self):
        self.run_fixture()

    def test_corruption_preserves_existing_output(self):
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            self.run_fixture(bad_hash=True)

    def test_archive_path_traversal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'archive contents'):
            self.run_fixture(member='../escaped')

    def test_x86_binary_is_rejected(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_fixture(machine=62)


if __name__ == '__main__':
    unittest.main()
