# Codemagic ARM64 build and device validation

This branch supplies two **manual** workflows in `codemagic.yaml`. They do
not trigger GitHub Actions or publish to an app store. Add this GitHub
repository to Codemagic and select `feature/arm64-runtime` (or the PR's branch).

1. Start **ARM64 Codex guest (Linux)** on Codemagic's `linux` instance. It checks out the exact
   pinned Codex revision, builds the unmodified `codex-app-server` for
   `aarch64-unknown-linux-musl`, and packages the Alpine 3.24 aarch64 rootfs.
   Record the SHA-256 printed at the end and download/copy the direct artifact
   URL for `codexpad-runtime-arm64.tar.gz` from the finished build. The `.sha256`
   artifact contains the same value.
2. Start **CodexPad ARM64 iPad (unsigned IPA)** on `mac_mini_m2`, entering the
   rootfs artifact's HTTPS `runtime_url` and `runtime_sha256`. A private artifact
   needs a usable download link (for example a Codemagic signed artifact URL)
   that the second build can access. The workflow checks the archive and
   app-server ELF, initializes the pinned ARM64 fork and its submodules, stages
   the native UI into the fork's `iSH-ARM64` target, compiles with
   `CODE_SIGNING_ALLOWED=NO`, and verifies the archived guest bytes inside the
   unsigned IPA. Download `CodexPad-ARM64-unsigned.ipa` and sign/install it
   with your own normal unsigned-IPA workflow.

The ARM64 bundle identifier is `com.joshuasyson.CodexPad.arm64`; this protects
the installed i686 app and its guest data. No JIT/MAP_JIT entitlement is added
by the staging script. Check entitlements after signing on the device. The
staging script rejects a changed fork revision or Swift file set; update and
review its anchors when either source evolves. The two workflows preserve
Cargo registry, Rust toolchains, Zig and Homebrew caches without uploading
the large compiled Codex target directory.

## Local equivalent and evidence

On Linux, build the guest with `scripts/build-codex-arm64.sh`, then run
`scripts/package-runtime-arm64.sh` with `CODEX_BINARY` and `CODEX_SOURCE_DIR`
set. On macOS with Xcode and Meson/Ninja installed:

```sh
git submodule update --init --recursive upstream/ios-linuxkit
python3 scripts/prepare-arm64-ios.py --output artifacts/staged-arm64-ios
export CODEXPAD_ROOTFS_PATH="$PWD/artifacts/codexpad-runtime-arm64.tar.gz"
xcodebuild -project artifacts/staged-arm64-ios/iSH.xcodeproj \
  -scheme iSH-ARM64 -configuration Release -sdk iphoneos \
  -destination 'generic/platform=iOS' \
  -derivedDataPath "$PWD/artifacts/derived-arm64" \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO \
  CODE_SIGN_ENTITLEMENTS= build
```

An unsigned compile and ELF header checks do not establish that the Linux
guest boots, that Rust can spawn children, or that TLS and MCP work. Record
each device result, iOS version, fork revision, Codex revision, run duration
and failure logs in `ARM64_RUNTIME.md` before considering this PR ready.

| Gate | How to check on a signed physical iPad |
| --- | --- |
| Guest boot and toolchain | Terminal: `uname -m` (`aarch64`), `/bin/sh`, `git --version`, `python3 --version`, `rg --version`, `ssh -V` |
| DNS and HTTPS | `python3 -c 'import socket; print(socket.getaddrinfo("example.com",443))'` and `python3 -c 'import urllib.request; print(urllib.request.urlopen("https://example.com").status)'` |
| Subprocess, files, signals | Run the commands in `scripts/probe-arm64-runtime.sh` inside the ARM64 guest; repeat after suspend/resume |
| Host bridge and app-server | Native SwiftUI should connect to `ws://127.0.0.1:4500`, initialize, load account/model, list/read files and create a thread |
| Real Codex protocol | On an AArch64 Linux host, use the pinned fork, packaged rootfs and `scripts/probe-ish-runtime.py --transport websocket --startup init` to exercise initialize, account/read, model/list, fs/readDirectory, shell tool and 20 repeated child launches; repeat on iPad through the native UI |
| Approvals and turns | With an authenticated account, create threads, run turns and shell tools, request/resolve approvals, interrupt a turn, and restart the app |
| Generic MCP | Register a test MCP server; exercise HTTP/SSE or Streamable HTTP, a tool call, browser OAuth handoff and callback, then test ChatCut's Codex MCP integration without bundling it |
| Performance | On the same iPad compare cold boot, `command/exec`, app-server initialize, `git status`, `rg` against the i686 build; report median and range |

The Codemagic builds and device gates were **not run** in this development
environment; no Xcode, Docker daemon, AArch64 host or connected iPad is
available here. A Codemagic compile or device failure is a blocker to promoting
this draft PR. Keep the i686 variant available while closing these gates.
