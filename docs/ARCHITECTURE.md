# CodexPad architecture

CodexPad now uses one ARM64 guest path only.

```text
SwiftUI workspace
      |
      | JSON-RPC v2 / ws://127.0.0.1:4500
      v
Codex app-server (aarch64-unknown-linux-musl)
      |
      | Linux syscalls
      v
ARM64 ios-linuxkit guest -> Alpine aarch64 -> app filesystem / Files bridge
```

## Layers

1. **Native workspace** — SwiftUI views, models, approvals, settings, Files/workspace integration and the Codex protocol client in `app/CodexPad`.
2. **Codex app-server** — upstream Rust `codex-app-server`, cross-compiled for `aarch64-unknown-linux-musl`.
3. **ARM64 guest** — pinned `rcarmo/ios-linuxkit` runtime with an Alpine aarch64 rootfs and the Codex guest overlay.

The former root iSH x86 emulator, i686 target, compatibility patch layer and x86 instruction tests are intentionally not part of the active architecture anymore.

## Staging model

`scripts/prepare-arm64-ios.py` copies the pinned ARM64 upstream into a disposable build directory and injects only the CodexPad-specific host layer:

- all Swift files from `app/CodexPad` plus the protocol schema;
- `app/SceneDelegate.m`;
- `app/AppGroup.m`;
- `app/Info.plist`;
- the bundle identifier from `app/iSH.xcconfig`.

The ARM64 upstream supplies the kernel/runtime implementation, terminal, Files support, lifecycle code and Xcode project. This keeps CodexPad-specific changes small and avoids maintaining a second legacy emulator tree.

## Runtime pins

`Dependencies/arm64-runtime.json` is the source of truth for the ARM64 runtime revision, Alpine release/rootfs checksum and guest target. `Dependencies/upstreams.json` pins the Codex source/toolchain.

Current guest target: `aarch64-unknown-linux-musl`.

## Runtime contract

- Bind the app-server to guest/app loopback only.
- Complete Codex `initialize` / `initialized` before normal RPC use.
- Keep guest state and tool execution inside the selected ARM64 Linux root.
- Route approvals and user-input requests through native UI surfaces.
- Keep the terminal available as a recovery/diagnostic surface.
- Treat the iPad application container as the outer OS boundary; the Linux compatibility guest is not a separate desktop-style security sandbox.

## Build boundary

Hosted builds use the M2 workflow in `codemagic.yaml`. The workflow prepares the AArch64 runtime, stages the ARM64 Xcode project and produces an unsigned iPad artifact. The legacy i686/x86 GitHub workflows are not part of the supported build path.

## Limits

- iPadOS background suspension still applies.
- Provider-backed model inference normally requires network access.
- Physical-device runtime behavior must be verified separately from simulator/source-level checks.
- Upstream features requiring unsupported external host executables are not implied to work merely because the app-server runs.
