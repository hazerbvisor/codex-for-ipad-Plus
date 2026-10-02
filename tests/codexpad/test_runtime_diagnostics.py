import base64
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

PROJECT = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader('diagnostic', str(PROJECT / 'runtime/usr/local/libexec/codexpad/diagnose'))
spec = importlib.util.spec_from_loader(loader.name, loader)
diagnostic = importlib.util.module_from_spec(spec)
loader.exec_module(diagnostic)


def receive_request(connection):
    first, length = diagnostic.exact(connection, 2)
    assert first == 0x81 and length & 128
    length &= 127
    if length == 126:
        length = struct.unpack('!H', diagnostic.exact(connection, 2))[0]
    mask = diagnostic.exact(connection, 4)
    payload = diagnostic.exact(connection, length)
    return json.loads(bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload)))


class DiagnosticTests(unittest.TestCase):
    def probe(self, error_stage=None):
        # A real socket handshake and wire RPC responses; private sentinels must
        # never appear in the output, even in a rejected RPC's message.
        client, server = socket.socketpair()
        client.settimeout(2)
        server.settimeout(2)
        errors = []

        def serve():
            try:
                with server:
                    headers = b''
                    while not headers.endswith(b'\r\n\r\n'):
                        headers += server.recv(1)
                    key = next(line.split(b':', 1)[1].strip() for line in headers.split(b'\r\n')
                               if line.startswith(b'Sec-WebSocket-Key:'))
                    accept = base64.b64encode(hashlib.sha1(key + b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
                    server.sendall(b'HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: ' + accept + b'\r\n\r\n')
                    for stage in ('protocol.initialize', 'authentication.account.read'):
                        request = receive_request(server)
                        result = {'id': request['id'], 'result': {} if request['id'] == 1 else
                                  {'account': {'email': 'private-user@example.invalid', 'type': 'chatgpt'}}}
                        if error_stage == stage:
                            result = {'id': request['id'], 'error': {'message': 'private-token https://example.invalid/?state=private-state'}}
                        payload = json.dumps(result).encode()
                        server.sendall(b'\x81\x7e' + struct.pack('!H', len(payload)) + payload)
                        if error_stage == stage:
                            break
                        if request['id'] == 1:
                            receive_request(server)  # initialized notification
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=serve)
        thread.start()
        output = io.StringIO()
        http = MagicMock()
        http.getresponse.return_value.status = 200
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(output), \
                patch.object(diagnostic.socket, 'create_connection', return_value=client), \
                patch.object(diagnostic.http.client, 'HTTPConnection', return_value=http):
            diagnostic.diagnose(Path(temporary))
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertNotIn('private-', output.getvalue())
        return {row['stage']: row for row in map(json.loads, output.getvalue().splitlines())}

    def test_initialized_authenticated_server(self):
        rows = self.probe()
        self.assertEqual(rows['runtime']['status'], 'invalid')
        self.assertEqual(rows['runtime.code-mode-host']['status'], 'missing')
        self.assertEqual(rows['boot']['status'], 'not-started')
        self.assertEqual(rows['service']['status'], 'not-started')
        self.assertEqual(rows['server']['status'], 'pid-unavailable')
        self.assertEqual(rows['transport.websocket']['status'], 'ok')
        self.assertEqual(rows['protocol.initialize']['status'], 'ok')
        self.assertTrue(rows['authentication.account.read']['authenticated'])

    def test_protocol_rejection_is_separate_from_transport(self):
        rows = self.probe('protocol.initialize')
        self.assertEqual(rows['transport.websocket']['status'], 'ok')
        self.assertEqual(rows['protocol.initialize']['status'], 'rpc-rejected')
        self.assertNotIn('authentication.account.read', rows)

    def test_account_failure_is_separate_from_protocol(self):
        rows = self.probe('authentication.account.read')
        self.assertEqual(rows['protocol.initialize']['status'], 'ok')
        self.assertEqual(rows['authentication.account.read']['status'], 'rpc-rejected')

    def test_closed_connection_is_bounded(self):
        client, server = socket.socketpair()
        server.close()
        with client, self.assertRaises(diagnostic.ProbeFailure) as error:
            diagnostic.message(client)
        self.assertEqual(error.exception.status, 'closed')


if __name__ == '__main__':
    unittest.main()
