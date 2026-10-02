"""Regression for Xcode phase references appearing before their definitions."""
import importlib.util
import json
import plistlib
import re
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('staging', PROJECT / 'scripts/prepare-arm64-ios.py')
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)

SOURCE = 'AA0000081F96D90D00FFB7A4'
RESOURCE = 'AA000010AF96D90D00FFB7A4'
FIXTURE = '''
\t\tAA0000011F96D90D00FFB7A4 /* app target */ = {
\t\t\tisa = PBXNativeTarget;
\t\t\tbuildPhases = (
\t\t\t\tAA0000081F96D90D00FFB7A4 /* Sources */,
\t\t\t\tAA000010AF96D90D00FFB7A4 /* Resources */,
\t\t\t);
\t\t};
\t\tAA000010AF96D90D00FFB7A4 /* Resources */ = {
\t\t\tisa = PBXResourcesBuildPhase;
\t\t\tfiles = (
\t\t\t\tBBBBBBBBBBBBBBBBBBBBBBBB /* root.tar.gz in Resources */,
\t\t\t);
\t\t};
\t\tAA0000081F96D90D00FFB7A4 /* Sources */ = {
\t\t\tisa = PBXSourcesBuildPhase;
\t\t\tfiles = (
\t\t\t\tAAAAAAAAAAAAAAAAAAAAAAAA /* SceneDelegate.m in Sources */,
\t\t\t);
\t\t};
'''


class StagingTests(unittest.TestCase):
    def test_target_reference_does_not_redirect_swift_into_resources(self):
        original_resources = staging.object_definition(FIXTURE, RESOURCE).group()
        entry = '\t\t\t\tCCCCCCCCCCCCCCCCCCCCCCCC /* Host.swift in Sources */,\n'
        result = staging.insert_in_section(FIXTURE, SOURCE, entry, 'PBXSourcesBuildPhase')
        self.assertIn(entry, staging.object_definition(result, SOURCE).group())
        self.assertEqual(staging.object_definition(result, RESOURCE).group(), original_resources)
        self.assertIn('SceneDelegate.m in Sources', result)

    def test_wrong_phase_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'not PBXSourcesBuildPhase'):
            staging.insert_in_section(FIXTURE, RESOURCE, '', 'PBXSourcesBuildPhase')

    def test_duplicate_phase_definition_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'one definition'):
            staging.insert_in_section(FIXTURE + FIXTURE, SOURCE, '', 'PBXSourcesBuildPhase')

    @unittest.skipUnless((staging.FORK / 'iSH.xcodeproj/project.pbxproj').exists(),
                         'Initialize the pinned ios-linuxkit submodule for full staging verification')
    def test_real_staged_target_compiles_all_swift_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'staged'
            staging.stage(output)
            for name in ("AppIcon.appiconset", "CodexMark.imageset"):
                source_asset = PROJECT / "app/Assets.xcassets" / name
                staged_asset = output / "app/Assets.xcassets" / name
                contents = json.loads((staged_asset / "Contents.json").read_text())
                for image in contents["images"]:
                    filename = image["filename"]
                    self.assertEqual((staged_asset / filename).read_bytes(),
                                     (source_asset / filename).read_bytes())
            self.assertEqual((output / "app/Icons/icon.png").read_bytes(),
                             (PROJECT / "app/Icons/icon.png").read_bytes())
            icons = plistlib.loads((output / "app/Icons/Icons.plist").read_bytes())
            self.assertEqual(icons[""]["description"], "Codex")
            self.assertIn("ihash1", icons)  # Preserve existing alternate icons.
            self.assertEqual(sorted(x.name for x in (output / "app/Assets.xcassets").iterdir()
                                    if x.name not in ("AppIcon.appiconset", "CodexMark.imageset")),
                             sorted(x.name for x in (staging.FORK / "app/Assets.xcassets").iterdir()
                                    if x.name not in ("AppIcon.appiconset", "CodexMark.imageset")))
            original = (staging.FORK / 'platform/darwin.c').read_text()
            darwin = (output / 'platform/darwin.c').read_text()
            self.assertIn('dispatch_once', original)
            # Compile and exercise the real staged initializer as plain C.
            # Darwin API types are stubbed; pthread_once runs on the host.
            initializer = re.search(r'static mach_port_t cached_host =.*?\n}\n',
                                    darwin, re.S).group()
            initializer += re.search(r'static mach_port_t cached_host_self\(void\).*?\n}',
                                     darwin, re.S).group()
            initializer = initializer.replace('mach_port_t', 'fixture_port_t').replace(
                'mach_host_self', 'fixture_host_self').replace('MACH_PORT_NULL', 'FIXTURE_PORT_NULL')
            harness = Path(temporary) / 'once.c'
            harness.write_text('#include <pthread.h>\n#include <assert.h>\n'
                'typedef int fixture_port_t;\n#define FIXTURE_PORT_NULL 0\n'
                'static int calls;\nstatic int fixture_host_self(void) { ++calls; return 42; }\n'
                + initializer + '\n'
                'static void *worker(void *unused) { (void)unused; '
                'for (int i=0;i<1000;i++) assert(cached_host_self()==42); return 0; }\n'
                'int main(void) { pthread_t t[8]; '
                'for(int i=0;i<8;i++) assert(pthread_create(&t[i],0,worker,0)==0); '
                'for(int i=0;i<8;i++) assert(pthread_join(t[i],0)==0); '
                'assert(calls==1); return 0; }\n')
            executable = Path(temporary) / 'once'
            subprocess.run([shutil.which('cc') or 'cc', '-std=c11', '-Wall', '-Werror',
                            '-pthread', str(harness), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)
            pbx = (output / 'iSH.xcodeproj/project.pbxproj').read_text()
            sources = staging.phase_files(pbx, SOURCE, 'PBXSourcesBuildPhase').group(1)
            resources = staging.phase_files(pbx, RESOURCE, 'PBXResourcesBuildPhase').group(1)
            fork_info = plistlib.loads((staging.FORK / 'app/Info.plist').read_bytes())
            staged_info = plistlib.loads((output / 'app/Info.plist').read_bytes())
            self.assertEqual(staged_info['UIAppFonts'], fork_info['UIAppFonts'])
            for font in staged_info.pop('UIAppFonts'):
                self.assertIn(font + ' in Resources', resources)
                self.assertTrue(list((output / 'app').rglob(font)), font)
            self.assertEqual(staged_info, plistlib.loads((PROJECT / 'app/Info.plist').read_bytes()))
            for source in staging.SWIFT_FILES:
                self.assertEqual(sources.count(source.name + ' in Sources'), 1)
                self.assertNotIn(source.name, resources)
                self.assertEqual((output / 'app/CodexPad' / source.name).read_bytes(), source.read_bytes())
            self.assertIn('CodexClientRequest.schema.json in Resources', resources)
            self.assertIn('upstreams.json in Resources', resources)
            config = (output / 'app/AppARM64.xcconfig').read_text()
            self.assertIn('SWIFT_OBJC_INTERFACE_HEADER_NAME = CodexPadApp-Swift.h', config)
            self.assertIn('SWIFT_INSTALL_OBJC_HEADER = YES', config)
            self.assertIn('OTHER_LDFLAGS = $(inherited) -lsqlite3', config)
            self.assertIn('USE_XTERM_RENDERER=1', config)
            roots = (output / 'app/Roots.m').read_text()
            self.assertIn('URLByAppendingPathComponent:@"root.tar.gz"', roots)
            self.assertNotIn('URLForResource:@"root"', roots)
            self.assertEqual((output / 'app/CodexPadRuntimeRoot.inc').read_bytes(),
                             (PROJECT / 'scripts/CodexPadRuntimeRoot.inc').read_bytes())
            self.assertEqual((output / 'app/CodexPadRuntimeUpgrade.c').read_bytes(),
                             (PROJECT / 'scripts/CodexPadRuntimeUpgrade.c').read_bytes())
            delegate = (output / 'app/AppDelegate.m').read_text()
            self.assertLess(delegate.index('CodexPadPrepareRuntimeRoot'), delegate.index('mount_root(&fakefs'))
            download = (output / 'app/download-root.sh').read_text()
            self.assertNotIn('codexpad-runtime.tar.gz', download)
            self.assertNotIn('codexpad-runtime.tar.gz', resources)
            # A packaged frontend must resolve every local script and stylesheet.
            html = (output / 'app/terminal/xterm-term.html').read_text()
            for asset in re.findall(r'(?:src|href)="([^"]+)"', html):
                self.assertTrue((output / 'app/terminal' / asset).is_file(), asset)
            for asset in ('xterm-term.html', 'xterm-term-bridge.js', 'xterm-vendor'):
                self.assertIn(asset + ' in Resources', resources)
            controller = (output / 'app/TerminalViewController.m').read_text()
            activation = controller.split('- (void)codexPadActivateInput {', 1)[1].split('\n}', 1)[0]
            self.assertIn('if (self.sessionTerminal == nil)', activation)
            self.assertIn('[self startNewSession]', activation)
            self.assertIn('Guest runtime could not boot', activation)
            self.assertIn('self.terminal = self.sessionTerminal', activation)
            runtime_phase = staging.object_definition(pbx, 'AA000300AF96D90D00FFB7A4').group()
            self.assertIn('codexpad-build-runtime.sh', runtime_phase)
            self.assertNotIn('ln -sf', runtime_phase)
            for library in ('libish.a', 'libish_emu.a', 'libfakefs.a'):
                self.assertIn('$(BUILT_PRODUCTS_DIR)/' + library, runtime_phase)
            self.assertEqual((output / 'app/codexpad-build-runtime.sh').read_bytes(),
                             (PROJECT / 'scripts/build-arm64-ios-runtime.sh').read_bytes())


if __name__ == '__main__':
    unittest.main()
