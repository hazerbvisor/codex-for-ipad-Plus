#!/usr/bin/env bash
# Build the exact upstream app-server for the ARM64 Alpine guest.
set -euo pipefail

: "${CODEX_SOURCE_DIR:?Set CODEX_SOURCE_DIR to the pinned upstream Codex checkout}"
: "${CARGO_TARGET_DIR:?Set CARGO_TARGET_DIR to a persistent build directory}"
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
read_json() {
    python3 - "$1" "$2" <<'PY'
import json, sys
path, dotted = sys.argv[1:]
value = json.load(open(path, encoding="utf-8"))
for part in dotted.split("."):
    value = value[part]
print(value)
PY
}
expected="$(read_json "$project_root/Dependencies/upstreams.json" codex.revision)"
toolchain="$(read_json "$project_root/Dependencies/upstreams.json" codex.rustToolchain)"
target="$(read_json "$project_root/Dependencies/arm64-runtime.json" codexTarget)"
test "$target" = aarch64-unknown-linux-musl
test "$(git -C "$CODEX_SOURCE_DIR" rev-parse HEAD)" = "$expected" || {
    echo 'Codex checkout does not match the pinned revision' >&2; exit 1;
}
test "$(rustc --version | cut -d ' ' -f2)" = "$toolchain" || {
    echo "Rust $toolchain is required" >&2; exit 1;
}
command -v zig >/dev/null
command -v cargo-zigbuild >/dev/null

# Keep upstream Codex intact. Do not carry the i386 OpenSSL/atomic/seccomp
# patch across architectures; test Rust process launch in the actual guest.
cd "$CODEX_SOURCE_DIR/codex-rs"
cargo zigbuild --locked --release --target "$target" \
    -p codex-app-server --bin codex-app-server
binary="$CARGO_TARGET_DIR/$target/release/codex-app-server"
python3 "$project_root/scripts/check-arm64-elf.py" --static "$binary"
file "$binary"
python3 - "$binary" <<'PY'
import hashlib, pathlib, sys
path = pathlib.Path(sys.argv[1])
h = hashlib.sha256()
with path.open("rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        h.update(chunk)
print(f"{h.hexdigest()}  {path}")
PY
