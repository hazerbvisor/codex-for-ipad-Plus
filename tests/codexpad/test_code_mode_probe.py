"""The probe must reject failed hosts without exposing their payloads."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('probe', PROJECT / 'scripts/probe-code-mode-host.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class CodeModeProbeTests(unittest.TestCase):
    def test_private_host_rejection_is_redacted(self):
        # Only a protocol failure fixture; production probes run the official host.
        program = '''
import sys,struct,json
def receive():
    n=struct.unpack('<I',sys.stdin.buffer.read(4))[0]
    sys.stdin.buffer.read(n)
def send(value):
    b=json.dumps(value).encode()
    sys.stdout.buffer.write(struct.pack('<I',len(b))+b);sys.stdout.buffer.flush()
receive()
send(dict(type='connection/ready',selectedVersion=1))
receive()
print('private-token https://example.invalid/?state=private-state',file=sys.stderr)
send(dict(type='operation/response',id=1,result=dict(status='error',message='private-user@example.invalid')))
'''
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertFalse(probe.probe([sys.executable, '-c', program], timeout=2))
        self.assertNotIn('private-', output.getvalue())
        rows = [json.loads(row) for row in output.getvalue().splitlines()]
        self.assertEqual(rows[-1], dict(stage='code-mode-host.session', status='failed'))

    def test_closed_host_fails_at_handshake(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertFalse(probe.probe([sys.executable, '-c', 'pass'], timeout=2))
        self.assertEqual(json.loads(output.getvalue()), dict(stage='code-mode-host.handshake', status='failed'))


if __name__ == '__main__':
    unittest.main()
