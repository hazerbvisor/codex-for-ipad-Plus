#!/usr/bin/env python3
"""Probe official Codex pipe/PTY commands in an already provisioned disposable root."""
import argparse
import base64
import json
from pathlib import Path
import queue
import subprocess
import threading
import time


def probe(args):
    command = ([args.emulator] if args.emulator else []) + [
        str(args.runtime.resolve()), "-r", str(args.root.resolve()), args.engine,
    ]
    with args.log.open("w") as log:
        engine = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=log, text=True)
        messages = queue.Queue()

        def read():
            for line in engine.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    pass
            messages.put(None)

        threading.Thread(target=read, daemon=True).start()
        sequence = 0
        notifications = []

        def rpc(method, params):
            nonlocal sequence
            sequence += 1
            engine.stdin.write(json.dumps({"id": sequence, "method": method, "params": params}) + "\n")
            engine.stdin.flush()
            deadline = time.monotonic() + 60
            while True:
                message = messages.get(timeout=max(0, deadline - time.monotonic()))
                assert message is not None, f"Engine exited; inspect {args.log}"
                if message.get("id") == sequence:
                    assert "error" not in message, message
                    return message["result"]
                notifications.append(message)

        def run(command, **extra):
            return rpc("command/exec", {
                "command": command, "cwd": "/root/workspace", "timeoutMs": 10000,
                "sandboxPolicy": {"type": "dangerFullAccess"}, **extra,
            })

        try:
            rpc("initialize", {"clientInfo": {"name": "terminal-regression", "version": "1"},
                               "capabilities": {"experimentalApi": True}})
            engine.stdin.write('{"method":"initialized"}\n')
            engine.stdin.flush()
            assert run(["/bin/true"]) == {"exitCode": 0, "stdout": "", "stderr": ""}
            result = run(["/bin/sh", "-c", "pwd; printf fixture-stderr >&2; exit 7"])
            assert result == {"exitCode": 7, "stdout": "/root/workspace\n", "stderr": "fixture-stderr"}, result
            result = run(["/bin/sh", "-c", "pwd"], tty=True, processId="terminal-fixture")
            assert result["exitCode"] == 0, result
            output = b"".join(base64.b64decode(message["params"]["deltaBase64"])
                              for message in notifications
                              if message.get("method") == "command/exec/outputDelta"
                              and message["params"]["processId"] == "terminal-fixture")
            assert b"/root/workspace" in output, (result, notifications)
            print("PASS official engine: pipe launch, cwd, stdout/stderr, exit status, PTY output")
        finally:
            engine.terminate()
            try:
                engine.wait(timeout=5)
            except subprocess.TimeoutExpired:
                engine.kill()
                engine.wait()
            engine.stdin.close()
            engine.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True, help="Staged ARM64 iSH CLI binary")
    parser.add_argument("--root", type=Path, required=True, help="Disposable guest root with engine and devices")
    parser.add_argument("--engine", default="/codex-app-server", help="Engine path inside the guest")
    parser.add_argument("--emulator", help="Optional ARM64 host emulator, e.g. qemu-aarch64-static")
    parser.add_argument("--log", type=Path, required=True, help="Runtime stderr/strace output")
    probe(parser.parse_args())


if __name__ == "__main__":
    main()
