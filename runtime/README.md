# CodexPad ARM64 guest overlay

These files are layered into the pinned Alpine **aarch64** root filesystem by `scripts/package-runtime-arm64.sh`.

The guest contains the ARM64 Linux `codex-app-server`, startup glue and the small runtime configuration needed by CodexPad. Keep guest-specific changes here instead of forking the upstream Codex Rust workspace.

The app-server is intended to listen only on guest/app loopback. The packaged runtime should be validated with the ARM64 runtime and WebSocket probes under `scripts/` before being embedded into an iPad build.

The legacy Alpine x86/i686 packaging path is no longer supported by this source tree.

In Terminal, run `/usr/local/libexec/codexpad/diagnose` for read-only JSON status
lines covering runtime files, boot markers, OpenRC, process state, health/readiness,
WebSocket initialization and account availability. It never reads saved auth
files, starts a login, or prints RPC payloads, account identifiers or URLs.
Markers and a PID alone do not establish that the server is ready; use the
health and protocol results together. See `docs/sign-in-pmull-repair.md` for
the verified failure and the remaining on-device checks.
