"""Verify runtime build failures cannot be hidden by archive publication.

External compiler/build commands are mocked; real Xcode remains the build gate.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
MOCK = '''#!/usr/bin/env python3
import json, os, pathlib, struct, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['FIXTURE_LOG'], 'a') as f: f.write(name+' '+json.dumps(args)+'\\n')
if name == 'xcrun':
    if '--show-sdk-path' in args: print('/mock/iPhoneOS.sdk')
    elif '--find' in args: print('/mock/bin/'+args[-1])
    elif 'lipo' in args:
        # -verify_arch consumes all following arguments as architectures.
        assert args[2:] == ['-verify_arch', 'arm64'], args
        assert pathlib.Path(args[1]).is_file(), args
        sys.exit(9 if os.environ.get('FIXTURE_BAD_ARCH') else 0)
elif name == 'meson':
    if os.environ.get('FIXTURE_MESON_FAIL'): sys.exit(7)
    if os.environ.get('SDKROOT'): raise SystemExit('Native compiler inherited iOS SDKROOT')
    cross = pathlib.Path(args[args.index('--cross-file')+1]).read_text()
    assert "'arm64-apple-ios17.0'" in cross
    assert "'/mock/iPhoneOS.sdk'" in cross
elif name == 'ninja':
    if os.environ.get('FIXTURE_NINJA_FAIL'): sys.exit(8)
    build = pathlib.Path(args[args.index('-C')+1])
    for library in ('libish.a', 'libish_emu.a', 'libfakefs.a'):
        if library == os.environ.get('FIXTURE_MISSING'): continue
        (build / library).write_bytes(b'!<arch>\\nmock archive')
    vdso = build / 'vdso/arm64'
    vdso.mkdir(parents=True)
    elf = bytearray(64)
    elf[:6] = b'\\x7fELF\\x02\\x01'
    struct.pack_into('<H', elf, 18, 183)
    (vdso / 'libvdso.so.elf').write_bytes(b'' if os.environ.get('FIXTURE_EMPTY_VDSO') else elf)
'''


class RuntimeBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        tools = self.root / 'tools'
        tools.mkdir()
        mock = tools / 'mock'
        mock.write_text(MOCK)
        mock.chmod(0o755)
        for name in ('xcrun', 'meson', 'ninja'):
            (tools / name).symlink_to(mock)
        app = self.root / 'source/app'
        app.mkdir(parents=True)
        (app / 'check-arm64-elf.py').write_bytes((PROJECT / 'scripts/check-arm64-elf.py').read_bytes())
        (app / 'summarize-build-failure.py').write_bytes(
            (PROJECT / 'scripts/summarize-build-failure.py').read_bytes())
        self.products = self.root / 'products'
        self.log = self.root / 'calls.log'
        self.env = dict(os.environ, PATH=str(tools)+os.pathsep+os.environ['PATH'],
                        SRCROOT=str(self.root / 'source'), MESON_BUILD_DIR=str(self.root / 'meson'),
                        BUILT_PRODUCTS_DIR=str(self.products), PLATFORM_NAME='iphoneos',
                        ARCHS='arm64', CONFIGURATION='Release', IPHONEOS_DEPLOYMENT_TARGET='17.0',
                        SDKROOT='/inherited/iOS.sdk', FIXTURE_LOG=str(self.log))

    def run_build(self):
        return subprocess.run(['bash', str(PROJECT / 'scripts/build-arm64-ios-runtime.sh')],
                              env=self.env, text=True, capture_output=True)

    def assert_unpublished(self):
        self.assertFalse(list(self.products.glob('*.a')))

    def test_all_archives_published_as_real_files(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ('libish.a', 'libish_emu.a', 'libfakefs.a'):
            archive = self.products / name
            self.assertGreater(archive.stat().st_size, 8)
            self.assertFalse(archive.is_symlink())
        self.assertEqual(self.log.read_text().count('"lipo"'), 3)

    def test_meson_failure_stops_before_ninja(self):
        self.env['FIXTURE_MESON_FAIL'] = '1'
        result = self.run_build()
        self.assertEqual(result.returncode, 7)
        self.assertNotIn('ninja ', self.log.read_text())
        self.assert_unpublished()

    def test_ninja_failure_is_not_masked(self):
        self.env['FIXTURE_NINJA_FAIL'] = '1'
        result = self.run_build()
        self.assertEqual(result.returncode, 8)
        self.assertIn('No compiler error or FAILED command found', result.stdout)
        self.assertTrue((self.root / 'meson/ninja-build.log').exists())
        self.assert_unpublished()

    def test_missing_archive_stops_before_publication(self):
        self.env['FIXTURE_MISSING'] = 'libfakefs.a'
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing or empty runtime archive', result.stderr)
        self.assert_unpublished()

    def test_wrong_architecture_stops_before_publication(self):
        self.env['FIXTURE_BAD_ARCH'] = '1'
        self.assertEqual(self.run_build().returncode, 9)
        self.assert_unpublished()

    def test_empty_vdso_cannot_ship(self):
        self.env['FIXTURE_EMPTY_VDSO'] = '1'
        self.assertNotEqual(self.run_build().returncode, 0)
        self.assert_unpublished()


if __name__ == '__main__':
    unittest.main()
