# Codemagic ARM64 build and device validation

The ARM64 pipeline now uses one **free-plan-compatible Apple-silicon M2**
workflow: `arm64-ipad-all-in-one`. It does not require a paid Linux worker and
it no longer passes a rootfs URL between two builds.

The workflow performs the complete chain on `mac_mini_m2`:

1. Install/cache Meson, Ninja, GNU host utilities, OpenSSL and zstd.
2. Download the pinned macOS ARM64 Zig and Rust toolchain.
3. Cross-build the pinned upstream `codex-app-server` for
   `aarch64-unknown-linux-musl` with `cargo-zigbuild`.
4. Build the pinned apk-tools 3.0.8 source natively for macOS. The native
   `apk` executable populates the Alpine AArch64 rootfs with
   `--root --arch aarch64 --no-scripts --no-commit-hooks`; no guest Linux
   executable is run on the Mac and Docker/nested virtualization is not needed.
5. Package and verify the AArch64 rootfs and static Codex ELF.
6. Initialize the pinned `ios-linuxkit` submodule, stage the existing CodexPad
   SwiftUI host into `iSH-ARM64`, compile without signing, and produce
   `CodexPad-ARM64-unsigned.ipa`.

The workflow is configured to run on pushes to `fix/codemagic-arm64-m2` for
validation and on `main` after merge. It also remains selectable manually in
Codemagic. A normal commit to the validation branch is intentionally used to
exercise the webhook path before merge. The build artifacts include the
unsigned IPA, rootfs archive and rootfs SHA-256.

The ARM64 bundle identifier remains `com.joshuasyson.CodexPad.arm64`, keeping
it separate from the existing i686 install. No JIT/MAP_JIT entitlement is
added by this pipeline.

## Why native apk-tools is used

Codemagic's free machine is `mac_mini_m2`; premium Linux machine types require
billing. The previous `arm64-guest` workflow therefore could not start on a
free account. Replacing `linux_x2` with `linux` was also invalid because
`linux` is not a Codemagic instance type.

The rootfs packager still supports its Docker path for Linux/local builders.
On Codemagic macOS, `APK_HOST_BINARY` selects the pinned native apk-tools
binary instead. Package scripts and commit hooks are disabled because they are
AArch64 Linux guest code and must not execute on the macOS build host.

## Validation gates after a successful IPA build

| Gate | Check on a signed physical iPad |
| --- | --- |
| Guest identity | `uname -m` must report `aarch64` |
| Basic tools | `/bin/sh`, `git --version`, `python3 --version`, `rg --version`, `ssh -V` |
| DNS and HTTPS | Resolve a public hostname and perform an HTTPS GET from the guest |
| App-server bridge | Native SwiftUI connects to `ws://127.0.0.1:4500` and initializes the real Codex server |
| Process behavior | Repeated child launches, signals, suspend/resume and app restart |
| Files integration | Linked folder bookmark mounts and survives restart |
| Codex protocol | account/model reads, threads, turns, shell tools, approvals and interruption |
| Generic MCP | Register a test MCP server, then validate OAuth/tool calls before testing ChatCut |

Do not mark runtime/device gates as passed from a compile alone. Keep the i686
build available until the ARM64 IPA has passed the same device regression set.
