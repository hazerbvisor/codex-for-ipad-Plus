# CodexPad

CodexPad is a native iPadOS workspace for the open-source Codex coding agent. The current runtime is ARM64-only: the SwiftUI host talks over app-local WebSocket to `codex-app-server` built for `aarch64-unknown-linux-musl`, running inside the pinned ARM64 `ios-linuxkit` guest with an Alpine aarch64 filesystem.

> CodexPad is an independent community port, not an official OpenAI or iSH app. It is an actively developed unsigned preview.

## Current architecture

```text
iPad ARM64
  -> native SwiftUI CodexPad UI
  -> ws://127.0.0.1:4500
  -> ARM64 ios-linuxkit guest
  -> Alpine aarch64
  -> codex-app-server (aarch64-unknown-linux-musl)
```

The legacy i686/x86 iSH emulator, x86-specific runtime patches, i686 Codex workflows, and emulator tests have been removed from the active source tree. `upstream/ios-linuxkit` is the sole guest runtime implementation.

## Source layout

- `app/CodexPad/` — native SwiftUI Codex workspace and protocol client.
- `app/SceneDelegate.m`, `app/AppGroup.m`, `app/Info.plist`, `app/iSH.xcconfig` — small host overrides copied into the staged ARM64 project.
- `upstream/ios-linuxkit` — pinned ARM64 Linux/iOS runtime.
- `runtime/` — Alpine guest overlay and Codex startup service.
- `scripts/prepare-arm64-ios.py` — creates a disposable ARM64 Xcode staging tree from the pinned upstream plus CodexPad UI.
- `scripts/build-codex-arm64.sh` — builds the Linux AArch64 Codex app-server.
- `scripts/package-runtime-arm64.sh` — packages the Alpine AArch64 runtime.
- `Dependencies/arm64-runtime.json` — pinned ARM64 runtime, Alpine and apk-tools metadata.
- `codemagic.yaml` — unsigned M2 Codemagic build path.

## Runtime pins

The ARM64 runtime currently targets Alpine 3.24 and `aarch64-unknown-linux-musl`. The pinned `ios-linuxkit` revision and rootfs checksum live in `Dependencies/arm64-runtime.json`; Codex source/toolchain pins live in `Dependencies/upstreams.json`.

## Building

### Codemagic

The supported hosted path is the M2 workflow in `codemagic.yaml`. It builds the AArch64 Codex runtime, stages the ARM64 iOS project, compiles the unsigned iPad app and exports the IPA artifact.

### macOS

Clone with submodules, prepare/package the ARM64 runtime, then use `scripts/prepare-arm64-ios.py` to generate the staged Xcode tree. Build the ARM64 target from that staged project. Do not use the removed root x86 iSH project.

## Verification

Architecture-independent native Codex model/RPC regressions remain under `tests/codexpad`. Runtime probes live under `scripts/`. Physical-device runtime and distribution behavior should still be treated as development-preview functionality until verified on device.

See `docs/ARCHITECTURE.md`, `docs/ARM64_RUNTIME.md`, `docs/CODEMAGIC_ARM64.md`, and `docs/VERIFICATION.md` for implementation details.

## Platform limits

- iPadOS may suspend long-running work in the background.
- The Linux guest is a compatibility environment, not a separate security boundary from the app container.
- Provider-backed model inference normally requires network access.
- Code Mode or other upstream components that require an unsupported separate host executable are not automatically provided by the ARM64 guest.

## License

This derivative app retains the applicable iSH GPL terms and additional iOS distribution permission in `LICENSE.md` and `LICENSE.IOS`. The bundled Codex executable is Apache-2.0; attribution is recorded in `THIRD_PARTY_NOTICES.md`.
