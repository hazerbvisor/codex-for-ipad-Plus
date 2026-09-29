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

# tikv-jemallocator 0.7.x currently breaks this macOS -> AArch64 musl
# cargo-zigbuild path: Rust code references the prefixed _rjem_* allocator
# symbols but the native jemalloc archive is not linked into the final binary.
# Codex only uses jemalloc as an optional global allocator optimization on musl;
# its protocol/runtime behavior does not depend on it. For the iPad guest build,
# use musl's normal system allocator instead. Keep this as a strict transient
# patch against the exact pinned Codex revision so upstream drift fails loudly.
python3 - "$CODEX_SOURCE_DIR/codex-rs/app-server/Cargo.toml" \
    "$CODEX_SOURCE_DIR/codex-rs/app-server/src/main.rs" <<'PY'
from pathlib import Path
import sys

cargo_path = Path(sys.argv[1])
main_path = Path(sys.argv[2])
cargo = cargo_path.read_text(encoding="utf-8")
main = main_path.read_text(encoding="utf-8")

dep_block = '''\n[target.'cfg(all(target_os = "linux", target_env = "musl", any(target_arch = "x86_64", target_arch = "aarch64")))'.dependencies]\ntikv-jemallocator = { workspace = true }\n'''
allocator_block = '''\n#[cfg(all(\n    target_os = "linux",\n    target_env = "musl",\n    any(target_arch = "x86_64", target_arch = "aarch64")\n))]\n#[global_allocator]\nstatic ALLOCATOR: tikv_jemallocator::Jemalloc = tikv_jemallocator::Jemalloc;\n'''

if cargo.count(dep_block) != 1:
    raise SystemExit("Pinned Codex app-server jemalloc dependency block changed; refusing blind patch")
if main.count(allocator_block) != 1:
    raise SystemExit("Pinned Codex app-server global allocator block changed; refusing blind patch")

cargo_path.write_text(cargo.replace(dep_block, "\n"), encoding="utf-8")
main_path.write_text(main.replace(allocator_block, "\n"), encoding="utf-8")
PY

grep -Fq 'tikv-jemallocator' "$CODEX_SOURCE_DIR/codex-rs/Cargo.lock"
! grep -Fq 'tikv-jemallocator' "$CODEX_SOURCE_DIR/codex-rs/app-server/Cargo.toml"
! grep -Fq 'tikv_jemallocator' "$CODEX_SOURCE_DIR/codex-rs/app-server/src/main.rs"

# Keep upstream Codex otherwise intact. Do not carry the old i386
# OpenSSL/atomic/seccomp patch across architectures; test process launch in the
# actual guest after packaging.
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
