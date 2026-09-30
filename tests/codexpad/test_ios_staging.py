"""Regression for Xcode phase references appearing before their definitions."""
import importlib.util
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
            pbx = (output / 'iSH.xcodeproj/project.pbxproj').read_text()
            sources = staging.phase_files(pbx, SOURCE, 'PBXSourcesBuildPhase').group(1)
            resources = staging.phase_files(pbx, RESOURCE, 'PBXResourcesBuildPhase').group(1)
            for source in staging.SWIFT_FILES:
                self.assertEqual(sources.count(source.name + ' in Sources'), 1)
                self.assertNotIn(source.name, resources)
                self.assertEqual((output / 'app/CodexPad' / source.name).read_bytes(), source.read_bytes())
            self.assertIn('CodexClientRequest.schema.json in Resources', resources)
            self.assertIn('upstreams.json in Resources', resources)
            config = (output / 'app/AppARM64.xcconfig').read_text()
            self.assertIn('SWIFT_OBJC_INTERFACE_HEADER_NAME = CodexPadApp-Swift.h', config)
            self.assertIn('SWIFT_INSTALL_OBJC_HEADER = YES', config)


if __name__ == '__main__':
    unittest.main()
