import importlib.util
from pathlib import Path
import tempfile
import unittest
import warnings
import zipfile

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('ipa_check', PROJECT / 'scripts/verify-ipa-runtime.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class IPARuntimeTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.runtime = Path(work.name) / 'runtime.tar.gz'
        self.runtime.write_bytes(b'verified runtime fixture')
        self.ipa = Path(work.name) / 'app.ipa'

    def package(self, entries):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.ipa, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name, data in entries:
                    archive.writestr(name, data)

    def test_single_matching_runtime(self):
        self.package([(check.ROOT, self.runtime.read_bytes())])
        check.verify(self.ipa, self.runtime)

    def test_second_runtime_copy_is_rejected(self):
        self.package([(check.ROOT, self.runtime.read_bytes()),
                      (check.LEGACY, self.runtime.read_bytes())])
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            check.verify(self.ipa, self.runtime)

    def test_duplicate_zip_entry_is_rejected(self):
        self.package([(check.ROOT, self.runtime.read_bytes())] * 2)
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            check.verify(self.ipa, self.runtime)

    def test_missing_runtime_is_rejected(self):
        self.package([])
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            check.verify(self.ipa, self.runtime)

    def test_wrong_runtime_is_rejected(self):
        self.package([(check.ROOT, b'stale runtime')])
        with self.assertRaisesRegex(ValueError, 'differs'):
            check.verify(self.ipa, self.runtime)


if __name__ == '__main__':
    unittest.main()
