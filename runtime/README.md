# CodexPad ARM64 guest overlay

These files are layered into the pinned Alpine **aarch64** root filesystem by `scripts/package-runtime-arm64.sh`.

The guest contains the ARM64 Linux `codex-app-server`, startup glue and the small runtime configuration needed by CodexPad. Keep guest-specific changes here instead of forking the upstream Codex Rust workspace.

The app-server is intended to listen only on guest/app loopback. The packaged runtime should be validated with the ARM64 runtime and WebSocket probes under `scripts/` before being embedded into an iPad build.

The legacy Alpine x86/i686 packaging path is no longer supported by this source tree.
