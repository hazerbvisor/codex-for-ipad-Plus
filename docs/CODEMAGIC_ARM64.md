# Codemagic ARM64 build and device validation

The ARM64 pipeline now uses one **free-plan-compatible Apple-silicon M2**
workflow: `arm64-ipad-all-in-one`. It does not require a paid Linux worker and
it no longer passes a rootfs URL between two builds.

The workflow performs the complete chain on `mac_mini_m2`:

1. Install/cache Meson, Ninja, GNU host utilities, OpenSSL and zstd.
2. Fetch the exact source revision and verify that the pinned official release
   tag resolves to that revision; run the complete native protocol gate.
3. Download the official `codex-app-server-aarch64-unknown-linux-musl.tar.gz`,
   verify its pinned SHA-256, extract only its expected regular-file member and
   require a static AArch64 ELF. Cache archives by checksum and reverify on reuse.
4. Build the pinned apk-tools 3.0.8 source natively for macOS. The native
   `apk` executable populates the Alpine AArch64 rootfs with
   `--root --arch aarch64 --no-scripts --no-commit-hooks`; no guest Linux
   executable is run on the Mac and Docker/nested virtualization is not needed.
5. Package and verify the AArch64 rootfs and static Codex ELF.
6. Initialize the pinned `ios-linuxkit` submodule, stage the existing CodexPad
   SwiftUI host into `iSH-ARM64`, verify all 14 Swift files belong to its Sources
   phase (and JSON files to Resources), compile without signing, and produce
   `CodexPad-ARM64-unsigned.ipa`.

The workflow is configured to run on pushes to `fix/official-arm64-app-server` for
validation and on `main` after merge. It also remains selectable manually in
Codemagic. A normal commit to the validation branch is intentionally used to
exercise the webhook path before merge. The build artifacts include the
unsigned IPA, rootfs archive, rootfs SHA-256 and app-server provenance JSON.

The ARM64 bundle identifier remains `com.joshuasyson.CodexPad.arm64`, keeping
it separate from the existing i686 install. No JIT/MAP_JIT entitlement is
added by this pipeline.

## Native iOS runtime archives

The staged ARM64 Meson phase calls `codexpad-build-runtime.sh` and declares
`libish.a`, `libish_emu.a` and `libfakefs.a` in `BUILT_PRODUCTS_DIR` as outputs.
The helper builds against the selected iOS SDK and arm64 deployment target,
clears inherited iOS SDK variables for native build tools, and propagates
Meson/Ninja errors. It requires a valid AArch64 ELF VDSO and checks all three
archives with `lipo` before copying real files into Xcode's products directory.
No symlink to a missing archive can satisfy the build phase. The app also links
SQLite, which the fake filesystem archive requires.

Homebrew LLVM and LLD supply the Linux AArch64 VDSO compiler/linker, while
Xcode's Clang and SDK compile the native iOS host runtime. These are different
binary formats with explicit target selection. Meson build scripts run with
Xcode user-script sandboxing disabled because they generate a dynamic build tree.

## Swift source staging

The staging script matches build-phase object definitions and validates their
Xcode types, rather than matching an earlier target reference to the same ID.
This prevents Swift source entries from landing in Copy Bundle Resources and
allows Xcode to generate `CodexPadApp-Swift.h` for `SceneDelegate.m`. The target
explicitly sets Swift 5 mode and the Objective-C interface header name.
`tests/codexpad/test_ios_staging.py` runs after runtime submodule initialization
to exercise the full staging path and check source/resource membership.

## Official app-server pin

`Dependencies/upstreams.json` pins `rust-v0.155.0-alpha.4` to source commit
`66eab8ece44141ff92707868269e1d53b40c4ac5`. This is an intentional
protocol-compatible alpha release, rather than a floating latest download.
Its protocol matches the existing GUI without changes: 166 client methods,
11 server requests, 84 notifications and 79 required schema tokens.
The archive SHA-256 is
`a7f84702edac562d5bb4827507f9658cb789e76320eefd3d142eaf7a86115f86`.

The M2 workflow no longer installs Rust, Zig or cargo-zigbuild, compiles Codex,
or patches its allocator. Existing compiler caches remain preserved for local
source-build fallback. The official executable retains upstream's allocator.
The packager verifies its provenance against the configured source revision
and actual binary hash, and includes both provenance and upstream LICENSE.
Checksum and ELF checks establish artifact identity and format; guest execution
and physical-device compatibility still require the gates below.

To stage a verified download locally:

```sh
python3 scripts/download-codex-arm64.py --output artifacts/codex-app-server
```

## Why native apk-tools is used

Codemagic's free machine is `mac_mini_m2`; premium Linux machine types require
billing. The previous `arm64-guest` workflow therefore could not start on a
free account. Replacing `linux_x2` with `linux` was also invalid because
`linux` is not a Codemagic instance type.

The rootfs packager still supports its Docker path for Linux/local builders.
On Codemagic macOS, `APK_HOST_BINARY` selects the pinned native apk-tools
binary instead. The host executable embeds `libapk` through Meson
`-Ddefault_library=static`; OpenSSL, zlib and zstd remain Homebrew host
dependencies. A link-mode cache key rebuilds the previous shared-library
installation, and `otool -L` plus `apk --version` must pass before caching.
This avoids copying an executable with an unresolved
`@rpath/libapk.3.0.0.dylib`. Package scripts and commit hooks are disabled because they are
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
