"""Exercise bootstrap/cache control flow with mocked macOS build commands.

These tests do not claim to compile or execute Mach-O on the Linux test host.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((PROJECT / 'Dependencies/arm64-runtime.json').read_text())['apkTools']
MOCK = '''#!/usr/bin/env python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
config = json.loads(os.environ['FIXTURE_CONFIG'])
if name == 'uname':
    print('Darwin' if args == ['-s'] else 'arm64')
elif name == 'jq':
    print(config[args[1].split('.')[-1]])
elif name == 'git':
    if args[-2:] == ['rev-parse', 'HEAD']: print(config['revision'])
elif name == 'brew':
    print('/fixture/homebrew')
elif name == 'meson':
    with open(os.environ['FIXTURE_LOG'], 'a') as f: f.write(json.dumps(args)+'\\n')
    path = pathlib.Path(args[1]) / 'src'
    path.mkdir(parents=True)
    apk = path / 'apk'
    apk.write_text('#!/bin/sh\\n'+ ('exit 1\\n' if os.environ.get('FIXTURE_BAD_VERSION') else 'echo apk-tools '+config['version']+'\\n'))
    apk.chmod(0o755)
elif name == 'otool':
    print(sys.argv[-1]+':')
    print('    @rpath/libapk.3.0.0.dylib' if os.environ.get('FIXTURE_DYNAMIC') else '    /usr/lib/libSystem.B.dylib')
'''


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        tools = self.root / 'tools'
        tools.mkdir()
        mock = tools / 'mock'
        mock.write_text(MOCK)
        mock.chmod(0o755)
        for name in ('uname', 'jq', 'git', 'brew', 'meson', 'ninja', 'pkg-config', 'otool'):
            (tools / name).symlink_to(mock)
        self.cache = self.root / 'cache'
        self.log = self.root / 'meson.log'
        self.env = dict(os.environ, PATH=str(tools)+os.pathsep+os.environ['PATH'],
                        APK_TOOLS_CACHE_DIR=str(self.cache), FIXTURE_CONFIG=json.dumps(CONFIG),
                        FIXTURE_LOG=str(self.log))

    def run_bootstrap(self):
        return subprocess.run(['bash', str(PROJECT / 'scripts/build-apk-tools-macos.sh')],
                              env=self.env, text=True, capture_output=True)

    def test_old_cache_rebuilt_and_valid_cache_reused(self):
        (self.cache / 'bin').mkdir(parents=True)
        apk = self.cache / 'bin/apk'
        apk.write_text('#!/bin/sh\necho apk-tools '+CONFIG['version']+'\n')
        apk.chmod(0o755)
        (self.cache / 'revision').write_text(CONFIG['revision']+'\n')
        result = self.run_bootstrap()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.log.read_text().splitlines()
        self.assertEqual(len(commands), 1)
        self.assertIn('-Ddefault_library=static', json.loads(commands[0]))
        self.assertEqual((self.cache / 'revision').read_text().strip(),
                         CONFIG['revision']+':static-libapk-v1')
        result = self.run_bootstrap()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Using cached', result.stdout)
        self.assertEqual(self.log.read_text().splitlines(), commands)

    def test_dynamic_libapk_rejected_without_cache_stamp(self):
        self.env['FIXTURE_DYNAMIC'] = '1'
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unbundled libapk', result.stderr)
        self.assertFalse((self.cache / 'revision').exists())

    def test_failed_version_probe_not_cached(self):
        self.env['FIXTURE_BAD_VERSION'] = '1'
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.cache / 'revision').exists())


if __name__ == '__main__':
    unittest.main()
