#!/usr/bin/env python3
"""Run the real helper's handshake/session/JavaScript smoke test without login.

Pass the command after --; it may include the guest emulator and root options.
Only fixed phase/status fields are logged, never host payloads or stderr.
"""
import argparse
import json
import os
import selectors
import struct
import subprocess
import time


def send(process, value):
    payload = json.dumps(value).encode()
    process.stdin.write(struct.pack('<I', len(payload)) + payload)
    process.stdin.flush()


def receive(process, timeout):
    deadline = time.monotonic() + timeout
    def exact(length):
        data = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while len(data) < length:
                if not selector.select(max(0, deadline - time.monotonic())):
                    raise TimeoutError()
                chunk = os.read(process.stdout.fileno(), length - len(data))
                if not chunk:
                    raise ValueError('closed')
                data.extend(chunk)
        return data
    length = struct.unpack('<I', exact(4))[0]
    if length > 1024 * 1024:
        raise ValueError('frame-limit')
    value = json.loads(exact(length))
    if not isinstance(value, dict):
        raise ValueError('invalid-response')
    return value


def value(response, kind, identifier):
    if response.get('type') != kind or response.get('id') != identifier:
        raise ValueError('invalid-response')
    result = response.get('result', {})
    if not isinstance(result, dict) or result.get('status') != 'ok':
        raise ValueError('host-rejected')
    return result.get('value', {})


def probe(command, timeout=45):
    stage = 'code-mode-host.spawn'
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, process_group=0,
                                   stderr=subprocess.DEVNULL)
        stage = 'code-mode-host.handshake'
        send(process, dict(type='connection/hello', supportedVersions=[1],
                           requiredCapabilities=[], optionalCapabilities=[]))
        ready = receive(process, timeout)
        if ready.get('type') != 'connection/ready' or ready.get('selectedVersion') != 1:
            raise ValueError('handshake-rejected')
        print(json.dumps(dict(stage=stage, status='ok')), flush=True)
        stage = 'code-mode-host.session'
        send(process, dict(type='operation/request', id=1,
                           request=dict(method='session/open', sessionId='codexpad-probe')))
        opened = value(receive(process, timeout), 'operation/response', 1)
        if opened.get('type') != 'session/ready':
            raise ValueError('invalid-response')
        print(json.dumps(dict(stage=stage, status='ok')), flush=True)
        stage = 'code-mode-host.execute'
        send(process, dict(type='operation/request', id=2,
                           request=dict(method='session/execute', sessionId='codexpad-probe',
                                        request=dict(tool_call_id='probe', enabled_tools=[],
                                                     source='text(String(6 * 7));',
                                                     yield_time_ms=30000, max_output_tokens=100))))
        started = value(receive(process, timeout), 'operation/response', 2)
        if started.get('type') != 'execution/started':
            raise ValueError('invalid-response')
        result = value(receive(process, timeout), 'execute/initialResponse', 2).get('Result', {})
        if result.get('error_text') is not None or result.get('content_items') != [dict(type='input_text', text='42')]:
            raise ValueError('execution-failed')
        print(json.dumps(dict(stage=stage, status='ok')), flush=True)
        return True
    except Exception as error:
        # Even upstream errors can contain private payloads: do not stringify them.
        status = 'timeout' if isinstance(error, TimeoutError) else 'failed'
        print(json.dumps(dict(stage=stage, status=status)), flush=True)
        return False
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except OSError:
                    pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=45)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or args.timeout <= 0:
        parser.error('Provide a helper command after -- and a positive timeout')
    raise SystemExit(0 if probe(command, args.timeout) else 1)
