# CodexPad Plus

**CodexPad Plus** is a native iPadOS workspace for the open-source Codex coding agent. It keeps the iPad UI native in SwiftUI while running the Codex app-server inside an ARM64 Linux guest based on `ios-linuxkit` and Alpine aarch64.

The project is moving toward a **Codex-for-macOS-inspired iPad experience**: thread sidebar, conversation workspace, model/reasoning controls, workbench, approvals, Files integration, keyboard-friendly desktop mode, and direct access to the underlying terminal when recovery or manual work is needed.

> **Status:** active development / unsigned preview. CodexPad Plus is an independent community project and is not an official OpenAI, Codex, iSH, or Apple application.

## Highlights

- Native SwiftUI iPad workspace instead of a terminal-only frontend.
- ARM64/AArch64 Linux runtime — no active i686 Codex runtime path.
- Official AArch64 Codex app-server pinned and checksum-verified by the build pipeline.
- macOS Codex-inspired split-view UI with searchable thread history.
- Native conversation composer with thread creation, resume, archive, interruption, and review support.
- Model catalog loaded from the active Codex provider.
- Reasoning-effort, service-tier, and collaboration-mode controls when exposed by the server.
- Native approval surfaces for commands, file changes, permission requests, and other Codex server requests.
- iPad Files folder linking into the Linux guest.
- Integrated file browser / preview and workbench surfaces.
- Touch mode and pointer + hardware-keyboard **Desktop mode**.
- Built-in terminal access for runtime inspection and recovery.
- Codemagic Apple-silicon build pipeline that exports an unsigned ARM64 IPA.

## Current architecture

```text
iPad ARM64
  │
  ├─ Native SwiftUI CodexPad Plus UI
  │    ├─ Threads / conversation
  │    ├─ Workbench / file browser
  │    ├─ Settings / model controls
  │    └─ Approvals / feature center
  │
  └─ app-local bridge
       ↓
     ws://127.0.0.1:4500
       ↓
     ARM64 ios-linuxkit guest
       ↓
     Alpine aarch64
       ↓
     codex-app-server
     (aarch64-unknown-linux-musl)
```

The old x86/i686 iSH-based Codex path has been removed from the active source tree. `upstream/ios-linuxkit` is the ARM64 guest runtime used by the current build.

## UI

CodexPad Plus uses an adaptive native workspace rather than embedding a desktop web app.

On wider iPad layouts it presents a Codex-style sidebar and conversation view with an optional workbench inspector. On compact layouts the conversation is prioritized and threads/workbench open as native sheets or navigation surfaces.

Current UI capabilities include:

- Searchable threads grouped by workspace path.
- New-thread and archive actions.
- Conversation history and active-turn state.
- Model and reasoning selection.
- Review of uncommitted changes.
- Pending approval/request surfaces.
- Plan, diff, files, runtime, and advanced feature views.
- Settings and account state.
- Touch-oriented mode for normal iPad use.
- Desktop mode for pointer + keyboard workflows with keyboard shortcuts and focus restoration.

## Authentication

The native client currently implements three Codex app-server authentication entry points:

1. **Continue with ChatGPT** — hosted browser sign-in using the Codex account login RPC.
2. **ChatGPT device code** — displays the device code and opens the verification page.
3. **OpenAI API key** — submits an API key through the Codex app-server login RPC.

Authentication is driven through upstream Codex `account/*` RPC methods instead of a separate custom provider layer.

Browser and device-code behavior still needs to be treated as **device-validation work until it has been verified end-to-end on the target iPadOS build**. The UI intentionally keeps alternate login methods available while those flows are being hardened.

## Models and Codex features

After the local engine connects, CodexPad Plus requests the model catalog from the active Codex provider and exposes the options returned by the server.

Depending on the selected model/server capabilities, the UI can expose:

- Model selection.
- Reasoning effort.
- Service tier.
- Collaboration modes.
- Review workflows.
- Thread / turn lifecycle operations.
- Filesystem operations.
- Command execution.
- Approval requests.
- Advanced RPCs through the Feature Center.

The Feature Center is intentionally capability-driven. An RPC being present in the pinned schema does **not** automatically mean that every upstream host-dependent feature works on iPadOS.

## Files integration

A folder from the iPadOS Files picker can be linked into the Linux guest and used as the Codex workspace.

The current guest mount path is:

```text
/root/workspaces/codexpad-files
```

CodexPad Plus keeps the native iPad folder selection flow while letting the Codex app-server operate on the mounted path from inside Alpine.

## MCP and plugins

The long-term goal is to preserve as much upstream Codex MCP/plugin behavior as the iPad runtime can support.

Current direction:

- Generic MCP support should be validated first against a simple test server.
- OAuth/tool-call behavior must be proven on a signed physical iPad.
- **ChatCut is not bundled with CodexPad Plus and should not yet be described as verified.** It is a target integration to test after the generic MCP path is stable.

## Source layout

```text
app/CodexPad/                 Native SwiftUI Codex workspace
app/SceneDelegate.m           Host integration override
app/AppGroup.m                App-group/runtime host glue
app/Info.plist                iPad application configuration
app/iSH.xcconfig              ARM64 host build overrides

upstream/ios-linuxkit/        Pinned ARM64 Linux/iOS runtime
runtime/                      Alpine guest overlay + Codex service
Dependencies/                 Pinned upstream/runtime metadata
scripts/                      Download, stage, package, and validation tools
tests/codexpad/               Native/protocol regression tests
docs/                         Architecture and build documentation
codemagic.yaml                Hosted unsigned IPA build pipeline
```

Important scripts:

- `scripts/prepare-arm64-ios.py` — creates the disposable staged ARM64 Xcode project.
- `scripts/download-codex-arm64.py` — downloads and verifies the pinned official AArch64 Codex app-server.
- `scripts/build-codex-arm64.sh` — optional local source-build fallback.
- `scripts/package-runtime-arm64.sh` — packages the Alpine AArch64 runtime.

## Runtime pins

The guest runtime and Codex app-server are intentionally pinned rather than floating on latest upstream artifacts.

- ARM64 runtime metadata: `Dependencies/arm64-runtime.json`
- Codex/upstream metadata: `Dependencies/upstreams.json`

The hosted build verifies the expected release/source relationship, archive checksum, ELF architecture, runtime packaging, and generated Xcode staging before exporting the IPA.

## Building with Codemagic

The supported hosted build is the Apple-silicon workflow in `codemagic.yaml`.

The pipeline performs the full ARM64 chain on a Codemagic M2 worker:

1. Install/cache required host build tools.
2. Verify the pinned Codex source/release metadata.
3. Download and checksum the official AArch64 musl app-server.
4. Build the pinned host `apk-tools` needed to assemble the Alpine filesystem.
5. Package and verify the AArch64 rootfs.
6. Initialize `upstream/ios-linuxkit`.
7. Stage the SwiftUI CodexPad host into the ARM64 runtime project.
8. Build without signing.
9. Export the unsigned IPA and runtime/provenance artifacts.

Typical final artifact:

```text
CodexPad-ARM64-unsigned.ipa
```

To download the pinned app-server locally:

```sh
python3 scripts/download-codex-arm64.py --output artifacts/codex-app-server
```

See [`docs/CODEMAGIC_ARM64.md`](docs/CODEMAGIC_ARM64.md) for the full hosted-build path.

## Building locally on macOS

Clone the repository with submodules, prepare/package the ARM64 runtime, and generate the staged iOS project with `scripts/prepare-arm64-ios.py`.

The generated ARM64 project is the supported Xcode build target. Do not use the removed legacy x86/i686 project path as the Codex runtime.

More detail is available in:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/ARM64_RUNTIME.md`](docs/ARM64_RUNTIME.md)
- [`docs/CODEMAGIC_ARM64.md`](docs/CODEMAGIC_ARM64.md)
- [`docs/VERIFICATION.md`](docs/VERIFICATION.md)

## Device validation

A successful compile proves that the application and packaged runtime were assembled correctly; it does **not** prove every guest/runtime feature on a physical iPad.

Important device gates include:

- `uname -m` reports `aarch64` inside the guest.
- Guest shell/process launching works repeatedly.
- DNS and HTTPS work inside Alpine.
- Native SwiftUI connects to the real app-server over loopback.
- ChatGPT/API authentication completes correctly.
- Model catalog and thread/turn RPCs work against the active account/provider.
- Commands, file writes, approvals, interruption, and review work end-to-end.
- Files bookmarks remount correctly after app restart.
- Suspend/resume does not corrupt the app-server/runtime state.
- Generic MCP works before provider-specific plugin testing.

## Platform limits

- iPadOS can suspend long-running work when the app moves to the background.
- Keep CodexPad Plus visible for long builds or agent turns when possible.
- The Linux guest is a compatibility/runtime environment inside the application container, not an independent security boundary.
- Network-backed model inference requires connectivity to the selected provider.
- Some upstream Codex features assume desktop host executables or OS integrations that may not exist on iPadOS.
- An unsigned IPA must be signed through an appropriate iOS/iPadOS signing workflow before installation on a physical device.

## Project goals

CodexPad Plus is trying to make the iPad feel like a serious native Codex workstation without pretending iPadOS is macOS.

The project priorities are:

1. Preserve upstream Codex app-server protocol behavior where practical.
2. Keep the primary UX native to iPadOS.
3. Run the Linux side as ARM64 instead of emulating an i686 Codex environment.
4. Keep builds reproducible through pinned sources and checksums.
5. Expose failures clearly instead of silently hiding unsupported behavior.
6. Add desktop-class workflows while retaining good touch input.
7. Expand MCP/plugin compatibility only after the core runtime is reliable.

## Contributing

Changes should be developed on feature/fix branches and reviewed through pull requests. Runtime, protocol, authentication, and build-system changes should include relevant regression coverage or validation notes where practical.

When changing pinned upstream components, update the corresponding metadata/checksums and document the compatibility impact.

## License

This derivative application retains the applicable iSH GPL terms and additional iOS distribution permission described in [`LICENSE.md`](LICENSE.md) and [`LICENSE.IOS`](LICENSE.IOS).

The bundled Codex executable is Apache-2.0. Third-party attribution is recorded in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
