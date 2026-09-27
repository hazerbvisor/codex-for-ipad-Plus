# Codemagic ARM64 build and device validation

The ARM64 pipeline now uses one **macOS M2** Codemagic workflow. This avoids the
paid Linux builders entirely, so an individual Codemagic account can use its
monthly free M2 minutes.

`arm64-ipad-unsigned` performs the whole build on `mac_mini_m2`:

1. installs/uses the pinned Rust, Zig and `cargo-zigbuild` toolchain;
2. checks out the exact pinned upstream Codex revision and cross-compiles
   `codex-app-server` for `aarch64-unknown-linux-musl`;
3. downloads the pinned Alpine 3.24 aarch64 minirootfs;
4. resolves the required Alpine `main`/`community` dependencies without
   executing Linux binaries on macOS and bundles their signed `.apk` files;
5. creates `codexpad-runtime-arm64.tar.gz` with the static app-server;
6. initializes the pinned `ios-linuxkit` submodule and stages the existing
   CodexPad SwiftUI host into its `iSH-ARM64` Xcode target;
7. builds without Apple code signing and emits
   `CodexPad-ARM64-unsigned.ipa`.

There is no Docker daemon, Linux Codemagic worker, QEMU package installer, or
cross-workflow artifact URL/sha handoff. The ARM64 guest installs the bundled
Alpine packages exactly once during its first sysinit, verifies their SHA-256
manifest first, and then continues with OpenRC. Alpine's own guest `apk`
verifies package signatures.

## Temporary validation trigger

While PR `fix/arm64-m2-codemagic` is under validation, the workflow watches
pushes to that branch. If the Codemagic app webhook is connected, pushing the
branch starts the build automatically. Remove or retarget that branch pattern
when the fix is merged so ordinary feature pushes do not consume build minutes.
If the webhook is not configured, choose the branch and
**CodexPad ARM64 iPad (M2, unsigned)** manually in Codemagic.

The resulting artifacts are:

- `artifacts/CodexPad-ARM64-unsigned.ipa`
- `artifacts/codexpad-runtime-arm64.tar.gz`
- `artifacts/codexpad-runtime-arm64.tar.gz.sha256`

The ARM64 bundle identifier remains `com.joshuasyson.CodexPad.arm64`, keeping
the saved ARM64 fakefs separate from the working i686 install. No JIT/MAP_JIT
entitlement is added by this build.

## Local equivalent

On an Apple-silicon Mac, install Meson/Ninja and the pinned Rust/Zig tooling,
then build the guest and app in the same order used by `codemagic.yaml`:

```sh
export CODEX_SOURCE_DIR="$PWD/.codex-local/codex-upstream"
export CARGO_TARGET_DIR="$PWD/.codex-local/cargo-target"
export CODEX_BINARY="$CARGO_TARGET_DIR/aarch64-unknown-linux-musl/release/codex-app-server"
export OUTPUT_ROOTFS="$PWD/artifacts/codexpad-runtime-arm64.tar.gz"

bash scripts/build-codex-arm64.sh
bash scripts/package-runtime-arm64.sh

git submodule update --init --recursive upstream/ios-linuxkit
python3 scripts/prepare-arm64-ios.py --output artifacts/staged-arm64-ios
export CODEXPAD_ROOTFS_PATH="$OUTPUT_ROOTFS"
xcodebuild -project artifacts/staged-arm64-ios/iSH.xcodeproj \
  -scheme iSH-ARM64 -configuration Release -sdk iphoneos \
  -destination 'generic/platform=iOS' \
  -derivedDataPath "$PWD/artifacts/derived-arm64" \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO \
  CODE_SIGN_ENTITLEMENTS= build
```

An unsigned compile and ELF checks establish build compatibility only. After
signing/installing the IPA on a physical iPad, close the runtime gates below
before replacing the i686 build.

| Gate | Physical-iPad validation |
| --- | --- |
| Guest identity | `uname -m` must report `aarch64` |
| First boot | bundled package SHA checks and `apk add --no-network` complete once |
| Guest tools | `/bin/sh`, Git, Python, ripgrep and SSH client run |
| DNS/HTTPS | guest name resolution and TLS request succeed |
| App bridge | SwiftUI connects to `ws://127.0.0.1:4500` and initializes the app-server |
| Codex protocol | account/model/files/thread/turn RPCs and repeated subprocess launches work |
| Files bridge | bookmarked iPad folder mounts and survives relaunch |
| Approvals | shell/tool approvals, interrupts and restart work |
| Generic MCP | HTTP/SSE or Streamable HTTP, OAuth callback and a test tool call work |
| ChatCut | validate only after generic MCP/OAuth works; do not hardcode ChatCut into the runtime |

Keep the i686 build available until these device gates pass.
