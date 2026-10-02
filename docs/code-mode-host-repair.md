# Missing Code Mode helper repair

## Root cause

The on-device banner reported that
`/usr/local/libexec/codexpad/codex-code-mode-host` was not found. The pinned
upstream `code-mode/src/remote_session.rs` checks whether that sibling executable
is a file before offering a process-owned Code Mode session. Its failure is
formatted as a spawn error even before a spawn is attempted.

CodexPad downloaded the standalone official app-server archive, whose only
member is the server. It did not download or install the separate helper. The
previous packaging gates likewise checked only the server. Upstream's release
workflow lists both executables in its app-server package.

The fix downloads the separate official helper from the **same existing**
`rust-v0.155.0-alpha.4` release/source revision. It retains the original server
pin and executable, installs the helper alongside it, and does not change
upstream feature defaults, hide warnings or enable controls artificially.

## Identity and packaging

`Dependencies/upstreams.json` pins the helper archive and extracted binary:

- Archive: `codex-code-mode-host-aarch64-unknown-linux-musl.tar.gz`
- Archive bytes: 24,828,724
- Archive SHA-256: `43f9f5312eccb39f49358de22cfce16bbd1071ce9556a296bde67d03e431e0be`
- Binary bytes: 64,496,632
- Binary SHA-256: `2797f9eb4457a7ee689b4eaead3981da32cce4ea3df6575800bca7de69b7ad47`

The archive digest matched GitHub's official release asset metadata. Download
and cache reuse verify it, reject unexpected members, verify static AArch64 ELF,
and verify the pinned binary hash. Packaging requires the helper plus provenance,
and records its checksum and `hasCodeModeHost` in the new runtime manifest. The
archive and actual-import gates reject missing helpers or mismatched metadata.

The locally produced single runtime measured **130,287,613 bytes (124.3 MiB)**,
with 334,215,357 expanded regular-file bytes. The original official server remains
183,797,680 bytes. This is a Linux packaging measurement using a local non-root
apk adapter, not a measured Codemagic IPA. No extra runtime archive, duplicate
server, native helper copy, padding or iOS JIT entitlement is introduced.

## Existing installations

Changing the bundle alone would leave previously imported guests without the
helper. Before mounting/booting, the native runtime preparation now checks the
helper's binary hash and fakefs executable metadata. For the matching Codex
revision, it streams only this managed executable from the bundled tar into the
existing guest, checks its hash/ELF, atomically renames it, and registers its
fakefs path/stat in a SQLite transaction. Copying only a host file would not make
it visible inside fakefs.

The selected root, server, service, account state, configuration, projects and
all other existing metadata remain in place. Interrupted file/metadata updates
are detected and retried; a valid installation is a no-op. A different installed
Codex revision produces an explicit upgrade error and retains that guest rather
than mixing incompatible releases. Existing invalid-server recovery still
imports a separate root and retains old roots. Legacy manifests need not be
rewritten: native validation checks the installed helper itself. Newly packaged
archives carry the new helper metadata.

## Execution evidence

The real official helper was tested under the repaired pinned ARM64 emulator
running through QEMU on Linux, using its actual framed stdio protocol:

1. Protocol V1 handshake passed.
2. Session creation passed.
3. JavaScript `text(String(6 * 7));` returned the expected text `42` without a
   runtime error.

This passed in a freshly imported new archive and in a previously imported old
archive upgraded by the actual C installer. In the upgrade test, the helper was
spawned by **guest Python**, with piped stdin/stdout and process group 0, matching
the upstream process-host setup. All existing fakefs path/stat rows and synthetic
credential/config/project contents were preserved. No real credentials or
provider requests were used, and no mock app-server or substitute helper was used.

`scripts/probe-code-mode-host.py` reproduces the handshake/session/execution
test. It logs only fixed phases/statuses; host payloads, errors and stderr are
not copied into diagnostics. Runtime diagnostics add a separate helper presence
stage for newly installed runtimes. Presence alone does not establish execution.

Offline tests cover verified helper downloads/cache reuse, corrupt archives and
binaries, wrong architecture, omitted files/metadata, different release provenance,
actual C installation, interrupted metadata registration, corrupt installed
helpers, symlinked directories, idempotence and preservation. Probe failure tests
check redaction. Existing PMULL/halfword and protocol gates remain intact.
All 59 Python tests, five halfword tests and two PMULL tests passed; the protocol
gate covered 166 methods, 11 server requests, 84 notifications and 79 GUI-routed
schema tokens. The actual C installer was compiled with `-Wall -Wextra -Werror`
on Linux. Archive-test CA input is a fixed public certificate, independent of
the macOS builder's Python trust-store setup; production guest trust is unchanged.

## Remaining device verification

macOS, Swift/Combine, Xcode and an iPad are unavailable here. The actual Objective-C
boot hook, iOS compilation, IPA and app-server-owned Code Mode execution during
an authenticated model turn have not been verified on device. The standalone
helper execution above is stronger evidence than compilation, but it does not
prove every delegated tool or emulator syscall works. Existing Linux
`command/exec` spawn limitations described in `sign-in-pmull-repair.md` are not
fixed by packaging this helper.

Install a future IPA built from this change over the existing app with the same
bundle identity; do not delete its data. Confirm the existing workspace/login
remain, open a new thread, and request an actual Code Mode JavaScript invocation
that emits `42`; confirm the tool result rather than a chat-only calculation.
The missing-host warning should be absent. For an independent Terminal check,
copy the probe script into the guest through Files and run:

```sh
python3 /root/workspace/probe-code-mode-host.py -- /usr/local/libexec/codexpad/codex-code-mode-host
```

Share only its fixed phase/status JSON, the error banner if present, and the
redacted native runtime log. Do not share credentials, raw server/host logs or
authorization URLs. No GitHub Actions or Codemagic build was started for this PR;
build/download caches were preserved.
