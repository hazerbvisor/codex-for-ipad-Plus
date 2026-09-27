#!/usr/bin/env bash
# Build the exact upstream app-server for the ARM64 Alpine guest.
set -euo pipefail

: "${CODEX_SOURCE_DIR:?Set CODEX_SOURCE_DIR to the pinned upstream Codex checkout}"
: "${CARGO_TARGET_DIR:?Set CARGO_TARGET_DIR to a persistent build directory}"
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
expected="$(jq -r '.codex.revision' "$project_root/Dependencies/upstreams.json")"
toolchain="$(jq -r '.codex.rustToolchain' "$project_root/Dependencies/upstreams.json")"
target="$(jq -r '.codexTarget' "$project_root/Dependencies/arm64-runtime.json")"
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
sha256sum "$binary"
