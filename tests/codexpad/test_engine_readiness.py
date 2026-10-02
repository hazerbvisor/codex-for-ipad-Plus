import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("engine_probe", PROJECT / "scripts/probe-ios-engine.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class EngineReadinessTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.report = Path(work.name) / "report.json"

    def write(self, ready, revision):
        self.report.write_text(json.dumps(dict(ready=ready, runtimeRevision=revision, events=[])))

    def test_ready_matching_runtime(self):
        self.write(True, "matching-revision")
        self.assertTrue(probe.wait_for_report(self.report, "matching-revision", timeout=1)["ready"])

    def test_connected_wrong_runtime_is_rejected(self):
        self.write(True, "wrong-revision")
        with self.assertRaisesRegex(RuntimeError, "different guest runtime"):
            probe.wait_for_report(self.report, "matching-revision", timeout=1)

    def test_failed_connection_is_rejected(self):
        self.write(False, "")
        with self.assertRaisesRegex(RuntimeError, "failed before readiness"):
            probe.wait_for_report(self.report, "matching-revision", timeout=1)

    def test_absent_report_is_bounded(self):
        with self.assertRaisesRegex(RuntimeError, "before the timeout"):
            probe.wait_for_report(self.report, "matching-revision", timeout=0)


if __name__ == "__main__":
    unittest.main()
