# ARM64 Linux guest migration

## Status (27 September 2026)

**ARM64 build integration is staged; no ARM64 iPad build has passed yet.**
`codemagic.yaml` provides a Linux guest build and a separate Apple silicon
unsigned iPad build. Both workflows still need their first real run. The
existing `iSH` target continues to compile x86, with its SwiftUI client,
i686 runtime, patches, and existing CI jobs retained.

The source of truth for the ARM64 candidate and rootfs is
`Dependencies/arm64-runtime.json`. `upstream/ios-linuxkit` is pinned to
`rcarmo/ios-linuxkit@61719f8177499f789fc2f7f421ea919ac7780608` (v2.1.3).
It is a separate submodule because its AArch64 instruction model, syscall ABI,
VDSO, memory structures, fakefs, and kernel are not interchangeable with this
repository's x86 Meson and Xcode source lists. Its transitive submodules need
initialization for a build. The staging script copies only the native host
integration into a build directory, leaving both source trees unchanged.

## Candidate comparison

| Candidate | Strength | Integration cost / uncertainty |
| --- | --- | --- |
| `Wind0ws11Aero/ish-arm64` | Original ARM64 guest fork, bind mounts, shell executor, Alpine guest | Older general-purpose fork; assess its exact Darwin socket, process and UI support before a pin change |
| `rcarmo/ios-linuxkit` | Maintained iOS Xcode target, AArch64 Linux build and staged tests, documented syscall/guest limits and rootfs architecture check | ARM64-only tree; host UI and Files bridge must be carried over, and its newer kernel must be tested against Codex |

The second candidate currently provides the less speculative ARM64 **guest
baseline**, but it is not a drop-in library for this repository. Both execute
an AArch64 Linux guest through a userspace kernel and precompiled ARM64
gadgets. Decoded blocks store function pointers and operands as data; this is
neither native Linux process execution nor unrestricted runtime code generation.
The source does not request `MAP_JIT` for its threaded-code execution. Confirm
the final unsigned IPA's entitlements and behavior on a physical iPad.

## Architecture and seams

Current: SwiftUI → guest loopback WebSocket `127.0.0.1:4500` → x86 iSH →
Alpine x86 → static i686 Codex app-server.

Target: the same SwiftUI protocol client → guest loopback WebSocket →
`ios-linuxkit` AArch64 userspace kernel / gadget interpreter → Alpine aarch64 →
static `aarch64-unknown-linux-musl` Codex app-server.

`scripts/prepare-arm64-ios.py` stages the fork's separate `iSH-ARM64` Xcode
target, which links its ARM64 `libish.a`, `libish_emu.a`, and `libfakefs.a`,
sets `guest_arch=arm64` and `GUEST_ARM64=1`, and uses the ARM64 kernel, VDSO,
syscall table and precompiled gadgets. It copies the existing
`app/CodexPad/*.swift` UI, original `SceneDelegate.m`, unsigned-signing
fallback `AppGroup.m`, Info.plist and request schema; it adds only the terminal
input and bookmarked folder hooks to the fork's newer host implementations.
Its `download-root.sh` copies the verified `CODEXPAD_ROOTFS_PATH` into the app.
The staged ARM64 app uses the original bundle ID plus `.arm64` so its saved
fakefs and credentials cannot be confused with the working i686 installation.
Verify the Files bookmark, file provider, guest startup and credential flows
on a signed physical device. The native protocol UI is unmodified.

The shared `runtime/etc/init.d/codexpad` still listens on guest loopback port
4500. The ARM64 packaging step enables it only when a validated static ARM64
Codex binary is supplied; a shell-only image has no misleading app-server
service. The Alpine minirootfs's inittab must match the expected OpenRC sysinit
entry before the narrow boot adapter is installed. Confirm OpenRC runlevels,
`/proc`, sockets and guest-to-host loopback on the exact iPad target. The
original `iOSFS` Files bridge is host code, not part of the Alpine tarball.

## Building an isolated guest image

On a Linux host with Docker, `jq`, `curl`, `tar` and Python 3:

```sh
git submodule update --init --recursive upstream/ios-linuxkit
OUTPUT_ROOTFS="$PWD/artifacts/codexpad-runtime-arm64.tar.gz" \
  scripts/package-runtime-arm64.sh
```

The script fetches the pinned Alpine 3.24 aarch64 minirootfs, extracts
AArch64 packages through `apk --root --arch aarch64 --no-scripts` on the host
architecture (without QEMU), installs Git, Python, ripgrep, OpenRC, SSH client
and certificates, checks ELF architecture,
and records a SHA-256. `ARM64_BASE_ROOTFS` may point to a locally cached
minirootfs. `CODEX_BINARY=/path/to/codex-app-server`
requires a static AArch64 ELF and enables the OpenRC service. Do not package
an iOS Mach-O executable or the old i686 ELF. The archive can grow substantially
with package dependencies; record its compressed size and reduce it only after
the working set is validated. Supply the resulting archive to an **ARM64**
Xcode target explicitly. The original Xcode target still defaults to x86.
When adding `CODEX_BINARY`, also set `CODEX_SOURCE_DIR` to the exact pinned
upstream checkout so the packaged licence and revision can be verified.

With an exact upstream checkout, Rust 1.95.0, Zig 0.14.1, cargo-zigbuild
0.23.4 and the ARM64 musl Rust target, run:

```sh
CODEX_SOURCE_DIR=/path/to/pinned/codex \
  CARGO_TARGET_DIR="$PWD/.codex-local/cargo-target" \
  scripts/build-codex-arm64.sh
```

The upstream pinned `codex-rs/core/Cargo.toml` already enables vendored
`openssl-sys` for aarch64 musl. This script deliberately builds upstream
without either i686 patch; successful linking and in-guest process/TLS probes
are still required. For Codemagic's two workflows and unsigned IPA, see
[`CODEMAGIC_ARM64.md`](CODEMAGIC_ARM64.md).

On an AArch64 Linux host with the fork's Meson/Ninja dependencies installed:

```sh
cd upstream/ios-linuxkit && make build-arm64-linux && cd ../..
ARM64_ROOTFS="$PWD/artifacts/codexpad-runtime-arm64.tar.gz" \
  ARM64_NETWORK_PROBE=1 scripts/probe-arm64-runtime.sh
```

The probe checks guest identity, command execution, Git, Python, ripgrep,
filesystem writes, repeated subprocesses, signals, and a guest-to-host
`127.0.0.1:4500` WebSocket handshake with a masked frame. DNS and HTTPS are
opt-in to avoid confusing network restrictions with emulator failures. This
Linux-host probe does not assert iOS behavior or OpenRC boot. A later gate
must handshake from the native app on a physical iPad with the real Codex
server, followed by repeated RPC calls and restart.

## Compatibility patch audit

| Existing item | Why it exists | ARM64 decision now |
| --- | --- | --- |
| `patches/codex-i686-musl-openssl.patch`: BLAKE3 portable build | i686 crypto support | i686 only; do not carry without a failing ARM64 build/probe |
| Same patch: vendored OpenSSL | static musl cross build | Pinned upstream already configures vendored OpenSSL for `aarch64-unknown-linux-musl`; verify linker and guest TLS on a real build |
| Same patch: x86 seccomp fallback | 32-bit filter unsupported | ARM64 guest also lacks general seccomp; retain fail-closed full-access policy, test behavior rather than installing a native Linux filter |
| `-DBROKEN_CLANG_ATOMICS` in i686 build | Zig i386 `__atomic_is_lock_free` link issue | i386-specific; test ARM64 linker before adding it |
| `patches/rust-1.95-ish-spawn.patch` | Rust Linux process launch expects `SOCK_SEQPACKET` and optionally pidfd | **Do not remove by architecture alone.** Probe ARM64 fork's Darwin socketpair and pidfd semantics, then run the pinned Rust worker-thread spawn smoke unpatched and patched as needed |
| i686 guest syscall/stdio/COW/poll/Darwin locking repairs | Functional iSH fixes | Compare with fork and repeat guest / Darwin stress gates; retain or port a repair if missing |

The pinned Codex commit remains `d77ebc72237a639b6d877f2edc3b20b54631f25e`
with Rust 1.95.0. Build it in an isolated checkout with target
`aarch64-unknown-linux-musl`; do not apply the x86 patch to that checkout.
First establish whether `openssl-sys` can be built static for AArch64, then
run the real Rust process-spawn probe under the selected emulator. Keep the
upstream app-server implementation and JSON-RPC protocol intact.

## Evidence and gates

| Gate | Result |
| --- | --- |
| Candidate source inspected / exact revision pinned | Yes, source audit only |
| Codemagic guest and iOS workflows, staged Xcode project | Source and static validation pass; hosted builds pending |
| ARM64 rootfs archive built and booted | Pending suitable Docker and ARM64 host |
| ARM64 iOS Xcode target builds and boots without JIT entitlement | Pending macOS/Xcode and physical iPad |
| `uname -m`, shell, Git, Python, ripgrep, DNS, TLS, process/signals | Pending execution of guest probe |
| Files bridge, OpenRC and localhost WebSocket 4500 | Pending iOS integration |
| Static ARM64 Codex app-server compile and initialize/account/model RPC | Pending ARM64 build and bridge |
| Threads, turns, shell tools, approvals and repeated spawns | Pending real Codex guest run |
| Generic MCP registration, HTTP/SSE/Streamable HTTP, OAuth callback and tool calls | Pending real Codex guest run |
| ChatCut MCP | Pending generic MCP and OAuth; do not hardcode |
| i686 versus ARM64 startup, shell, server, Git and rg benchmarks | Pending both iPad builds, same device and rootfs contents |

No Code Mode/V8 support is asserted. ARM64 guest limitations include incomplete
instruction coverage, missing Linux namespaces/cgroups/general seccomp,
incomplete socket/syscall/file edge cases, and iOS background suspension.
The iOS app sandbox is the outer boundary; Linux guest permissions are not
a hardened security boundary. Observe failures in the guest and the host
before treating any of these gates as passed.
