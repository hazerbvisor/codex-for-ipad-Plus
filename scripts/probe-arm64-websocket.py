#!/usr/bin/env python3
"""Check guest-to-host localhost WebSocket framing with no third-party modules."""

import argparse
import base64
import hashlib
import os
import socket
import subprocess
import time


GUEST = r'''
import base64, hashlib, socket
s=socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", 4500))
s.listen(1)
s.settimeout(20)
c,_=s.accept()
c.settimeout(10)
request=b""
while b"\r\n\r\n" not in request:
    request+=c.recv(4096)
key=next(line.split(":",1)[1].strip() for line in request.decode().split("\r\n") if line.lower().startswith("sec-websocket-key:"))
accept=base64.b64encode(hashlib.sha1((key+"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: "+accept+"\r\n\r\n").encode())
header=c.recv(2)
assert len(header)==2 and header[0]==0x81 and header[1]==0x82
mask=c.recv(4)
payload=c.recv(2)
assert len(mask)==4 and len(payload)==2
assert bytes(payload[i]^mask[i] for i in range(2))==b"hi"
c.sendall(b"\x81\x02ok")
c.close()
s.close()
'''


def check_websocket() -> None:
    key = base64.b64encode(os.urandom(16)).decode()
    for attempt in range(100):
        try:
            connection = socket.create_connection(("127.0.0.1", 4500), timeout=1)
            break
        except OSError:
            if attempt == 99:
                raise
            time.sleep(0.1)
    with connection:
        connection.settimeout(10)
        request = ("GET / HTTP/1.1\r\nHost: 127.0.0.1:4500\r\n"
                   "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        connection.sendall(request.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            response += connection.recv(4096)
        expected = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
        ).digest())
        assert response.startswith(b"HTTP/1.1 101 ") and expected in response, response
        mask = os.urandom(4)
        connection.sendall(b"\x81\x82" + mask + bytes(ord(c) ^ mask[i] for i, c in enumerate("hi")))
        assert connection.recv(4) == b"\x81\x02ok"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ish", help="AArch64 Linux-host ios-linuxkit binary")
    parser.add_argument("fakefs", help="Imported AArch64 Alpine fakefs directory")
    args = parser.parse_args()
    guest = subprocess.Popen([args.ish, "-f", args.fakefs, "/usr/bin/python3", "-c", GUEST],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        check_websocket()
        stdout, stderr = guest.communicate(timeout=15)
        if guest.returncode:
            raise RuntimeError(f"guest exited {guest.returncode}: {stdout!r} {stderr!r}")
    finally:
        if guest.poll() is None:
            guest.kill()
            guest.communicate()
    print("PASS: guest-to-host 127.0.0.1:4500 WebSocket handshake and masked frame")


if __name__ == "__main__":
    main()
