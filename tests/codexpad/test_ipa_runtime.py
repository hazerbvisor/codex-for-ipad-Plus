import importlib.util
import json
from pathlib import Path
import plistlib
import struct
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
        self.package(self.complete_app())
        report = check.verify(self.ipa, self.runtime)
        self.assertEqual(report['runtimeBytes'], self.runtime.stat().st_size)
        self.assertEqual(report['ipaBytes'], self.ipa.stat().st_size)
        with zipfile.ZipFile(self.ipa) as archive:
            self.assertEqual(report['appBytes'], sum(x.file_size for x in archive.infolist()))

    def complete_app(self):
        # Packaging fixtures; runtime contents are validated separately before bundling.
        info = dict(CFBundleExecutable='CodexPad', CFBundlePackageType='APPL',
                    UIAppFonts=list(check.FONTS), UIMainStoryboardFile='Terminal',
                    UILaunchStoryboardName='LaunchScreen')
        entries = [(check.ROOT, self.runtime.read_bytes()),
                   (check.APP + 'Info.plist', plistlib.dumps(info)),
                   (check.APP + 'CodexPad', struct.pack('<8I', 0xfeedfacf, 0x0100000c,
                                                       0, 2, 0, 0, 0, 0))]
        entries += [(check.APP + name, b'resource fixture') for name in check.RESOURCES]
        entries += [(check.APP + 'Base.lproj/' + name + '.storyboardc/Info.plist',
                     b'compiled storyboard fixture')
                    for name in ('Terminal', 'LaunchScreen', 'About', 'Roots')]
        entries += [(check.APP + 'upstreams.json', (PROJECT / 'Dependencies/upstreams.json').read_bytes()),
                    (check.APP + 'CodexClientRequest.schema.json',
                     (PROJECT / 'app/CodexPad/CodexClientRequest.schema.json').read_bytes())]
        return entries

    def test_runtime_only_ipa_is_rejected(self):
        self.package([(check.ROOT, self.runtime.read_bytes())])
        with self.assertRaisesRegex(ValueError, 'Info.plist'):
            check.verify(self.ipa, self.runtime)

    def test_each_required_native_resource_is_checked(self):
        for path, _ in self.complete_app():
            if path == check.ROOT:
                continue
            with self.subTest(path=path):
                self.package([(name, data) for name, data in self.complete_app() if name != path])
                with self.assertRaises(ValueError):
                    check.verify(self.ipa, self.runtime)

    def test_wrong_architecture_or_guest_executable_is_rejected(self):
        for binary in (b'\x7fELF' + b'\0' * 28,
                       struct.pack('<8I', 0xfeedfacf, 0x01000007, 0, 2, 0, 0, 0, 0)):
            with self.subTest(binary=binary[:8]):
                self.package([(name, binary if name == check.APP + 'CodexPad' else data)
                              for name, data in self.complete_app()])
                with self.assertRaisesRegex(ValueError, 'Mach-O'):
                    check.verify(self.ipa, self.runtime)

    def test_missing_font_registration_is_rejected(self):
        entries = self.complete_app()
        info = plistlib.loads(dict(entries)[check.APP + 'Info.plist'])
        del info['UIAppFonts']
        self.package([(name, plistlib.dumps(info) if name == check.APP + 'Info.plist' else data)
                      for name, data in entries])
        with self.assertRaisesRegex(ValueError, 'fonts are not registered'):
            check.verify(self.ipa, self.runtime)

    def test_stale_protocol_and_pins_are_rejected(self):
        for resource in ('upstreams.json', 'CodexClientRequest.schema.json'):
            with self.subTest(resource=resource):
                self.package([(name, json.dumps({}).encode() if name == check.APP + resource else data)
                              for name, data in self.complete_app()])
                with self.assertRaisesRegex(ValueError, 'protocol/pins differ'):
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
