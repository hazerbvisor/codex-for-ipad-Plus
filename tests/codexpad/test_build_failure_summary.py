import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('summary', PROJECT / 'scripts/summarize-build-failure.py')
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)


class SummaryTests(unittest.TestCase):
    def test_first_failed_command_and_error_survive_later_warnings(self):
        log = 'FAILED: kernel.p/problem.c.o\nclang -target arm64-apple-ios17.0 problem.c\nproblem.c:42:2: error: missing declaration\n' + '\n'.join('sock.c: warning: incompatible cast' for _ in range(300))
        output = summary.summarize(log)
        self.assertIn('FAILED: kernel.p/problem.c.o', output)
        self.assertIn('error: missing declaration', output)
        self.assertNotIn('No compiler error', output)
        self.assertLess(len(output.splitlines()), 20)

    def test_warning_only_tail_does_not_invent_cause(self):
        output = summary.summarize('sock.c: warning: incompatible cast\n18 warnings generated.\nninja: build stopped: subcommand failed.')
        self.assertIn('No compiler error or FAILED command found', output)

    def test_linker_and_fatal_header_errors_are_reported(self):
        output = summary.summarize("file.c:8:1: fatal error: 'missing.h' file not found\nld: library 'ish' not found")
        self.assertIn('fatal error:', output)
        self.assertIn("ld: library 'ish' not found", output)

    def test_codemagic_wrapper_preserves_exit_65_and_reprints_error(self):
        workflow = (PROJECT / 'codemagic.yaml').read_text()
        body = workflow.split('          if xcodebuild -project', 1)[1].split('\n          fi\n', 1)[0]
        script = 'set -euo pipefail\nif xcodebuild -project' + body + '\nfi\n'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'artifacts').mkdir()
            (root / 'scripts').mkdir()
            shutil.copy2(PROJECT / 'scripts/summarize-build-failure.py', root / 'scripts')
            stub = root / 'xcodebuild'
            stub.write_text('#!/bin/sh\necho "FAILED: example.o"\necho "example.c:1:1: error: compiler fixture"\nexit 65\n')
            stub.chmod(0o755)
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'], CM_BUILD_DIR=str(root))
            result = subprocess.run(['bash'],input=script,cwd=root,env=env,text=True,capture_output=True)
            self.assertEqual(result.returncode, 65, result.stderr)
            self.assertIn('Build failure diagnostics', result.stdout)
            self.assertGreaterEqual(result.stdout.count('error: compiler fixture'), 2)
            self.assertIn('error: compiler fixture', (root / 'artifacts/xcodebuild-arm64.log').read_text())


if __name__ == '__main__':
    unittest.main()
