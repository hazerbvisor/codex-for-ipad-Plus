#!/usr/bin/env bash
# Run on an AArch64 Linux machine with Meson/Ninja and the pinned fork built.
set -euo pipefail

: "${ARM64_ROOTFS:?Set ARM64_ROOTFS to a packaged AArch64 .tar.gz}"
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$project_root/upstream/ios-linuxkit"
expected="$(jq -r '.arm64Runtime.revision' "$project_root/Dependencies/upstreams.json")"
test "$(git -C "$source_dir" rev-parse HEAD)" = "$expected" || {
    echo 'ARM64 emulator is not at the pinned revision' >&2; exit 1;
}
case "$(uname -m)" in aarch64|arm64) ;; *) echo 'ARM64 host required for precompiled AArch64 gadgets' >&2; exit 1 ;; esac
test -x "$source_dir/build-arm64-linux/ish" || {
    echo 'First run make build-arm64-linux in upstream/ios-linuxkit' >&2; exit 1;
}
test -x "$source_dir/build-arm64-linux/tools/fakefsify"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
"$source_dir/build-arm64-linux/tools/fakefsify" "$ARM64_ROOTFS" "$work/fakefs"
ish="$source_dir/build-arm64-linux/ish"
run() { timeout 90 "$ish" -f "$work/fakefs" /bin/sh -c "$1"; }

run 'test "$(uname -m)" = aarch64 && python3 -c "import struct; assert struct.calcsize(\"P\") == 8"'
run 'test "$(printf hello | tr a-z A-Z)" = HELLO && git --version && python3 --version && rg --version | head -1'
run 'python3 -c "import pathlib,subprocess; p=pathlib.Path(\"/root/workspace/probe.txt\"); p.write_text(\"arm64\"); assert p.read_text()==\"arm64\"; [subprocess.run([\"/bin/sh\",\"-c\",\"exit 0\"],check=True) for _ in range(20)]"'
run 'python3 -c "import os,signal,subprocess; p=subprocess.Popen([\"/bin/sleep\",\"30\"]); p.send_signal(signal.SIGTERM); assert p.wait(timeout=10)!=0"'
python3 "$project_root/scripts/probe-arm64-websocket.py" "$ish" "$work/fakefs"
if [[ "${ARM64_NETWORK_PROBE:-0}" == 1 ]]; then
    run 'python3 -c "import socket; assert socket.getaddrinfo(\"example.com\",443)"'
    run 'python3 -c "import urllib.request; assert urllib.request.urlopen(\"https://example.com\",timeout=20).status==200"'
fi
echo 'PASS: ARM64 guest commands, filesystem, Git, Python, ripgrep, repeated subprocesses and signals'
echo 'DNS and HTTPS require ARM64_NETWORK_PROBE=1; iOS bridge checks remain separate.'
