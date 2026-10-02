# CodexPad IPA completeness audit

Audited main `25d8adfeb37fbe5c8ffaddd5bd2818665d65cf87`. Size alone cannot establish
runtime completeness or working sign-in.

## Why the IPA became smaller

The PR #17 implementation (`4e350215`) staged `download-root.sh` with an extra
`cp "$output" "$BUILT_PRODUCTS_DIR/$CONTENTS_FOLDER_PATH/codexpad-runtime.tar.gz"`.
`$output` was already `root.tar.gz` in the app. Both files contained the same guest.
PR #19 (`8fed0583`, merged as `5eeabd783cabd4877d1a0beea4aa1ec14c54bbce`)
removed that copy and made the importer explicitly use `root.tar.gz`. Recovery
was retained. The archive compression and IPA ZIP method were not changed by
that removal, and neither the server nor Terminal resources were removed.

PR #19 reports build #16 as approximately 209 MB with identical runtime copies.
That is historical reported artifact evidence; the older IPA was not downloaded
in this audit. Removing a second roughly 105 MB archive explains the reduction
to roughly 109 MB without requiring smaller binaries or omitted runtime tools.

## Measured evidence and access limits

The supplied Codemagic artifact returned HTTP 401. The supplied Drive file
`1J4eHKhhWPgbBbLCXnDkRpe_tdpafCMu9` also required authentication: the view
returned 401 and the download returned Google sign-in HTML, not an IPA.
Therefore **latest-main IPA compressed size, uncompressed app size, native
executable/resources and largest app files remain unmeasured**. No successful
IPA validation is claimed.

A cached runtime built during the PR #21 investigation was available. Its
producer scripts and runtime overlay match this main revision. It was produced
on Linux using a local non-root apk adapter, rather than the macOS CI environment;
it is independent evidence, not the inaccessible Codemagic IPA.

| Local runtime measurement | Bytes |
| --- | ---: |
| Compressed `root.tar.gz` | 105,454,745 |
| Expanded regular guest files (2,518 archive entries total) | 269,717,866 |
| Official AArch64 app-server | 183,797,680 |
| `libpython3.14.so.1.0` | 5,353,616 |
| `libcrypto.so.3` | 4,533,144 |
| Git | 3,213,592 |
| ripgrep | 3,152,312 |
| `libstdc++.so.6.0.34` | 2,820,600 |

The server has binary SHA-256
`0d7e590e54ea784c803ec563d5fd46a03e9fa52d4ed3ffe192c320a3e9786f9d`,
official release `rust-v0.155.0-alpha.4`, revision
`66eab8ece44141ff92707868269e1d53b40c4ac5`. Release archive SHA-256 is pinned
in `Dependencies/upstreams.json`. The runtime manifest matches that Codex pin,
ios-linuxkit `61719f8177499f789fc2f7f421ea919ac7780608`, Alpine 3.24 and
`aarch64-unknown-linux-musl`.

The actual local archive passed the strengthened runtime validator. Shell and
init links resolve to BusyBox; Bash, Python 3.14 with stdlib, Git and its HTTPS
helper, ripgrep, curl, musl loader, OpenRC, boot adapter, service, diagnostics,
profile and valid CA bundle are present. An independent ELF program-header audit
of all 264 ELF files found only AArch64 files, 39 distinct dynamic dependencies,
and no unresolved dependencies or interpreter paths in the guest library
directories. The real pinned fakefs importer imported the archive on Linux and
the existing imported-runtime validator passed. This does not establish that
every upstream operation is implemented by the emulator.

## Packaging and startup trace

`codemagic.yaml` downloads the checksum-pinned official server through
`download-codex-arm64.py`, preserving verified download caches. The APK bootstrap
preserves its cache. `package-runtime-arm64.sh` checks the pinned Alpine base,
installs AArch64 packages and dependencies, writes service/boot/CA wiring and
metadata, validates the archive, and emits one gzip tar archive. The fakefs
import gate checks the real importer before Xcode bundling.

`prepare-arm64-ios.py` stages the pinned ARM64 fork, applies the halfword and
PMULL repairs, compiles the native Swift sources and retains the Terminal
resource phase. `build-arm64-ios-runtime.sh` builds and checks the arm64 emulator
libraries that the native target links. The staged download phase copies the
verified runtime input to `root.tar.gz`. CI compares this bundled tar's hash to
the input before ZIP packaging; `verify-ipa-runtime.py` repeats that check and
rejects a duplicate/legacy runtime.

On launch, `Roots.m` imports that explicit bundle path. Runtime preparation runs
before fakefs mounting and boot; invalid roots recover to a separately imported
root without deleting old roots or their saved credentials. The saved boot
command defaults to `/sbin/init`. `inittab` runs the boot adapter and OpenRC's
default runlevel, whose service link starts the official server at
`ws://127.0.0.1:4500`. HOME and CODEX_HOME remain `/root` and `/root/.codex`.
The native client then performs protocol initialization and account reads.
These are traced source paths, not a successful iPad launch.

## Confirmed defect and checks added

Staging replaced the fork's Info.plist with CodexPad's, losing `UIAppFonts` even
though the four font files remained in the resource phase. The native font
picker calls `UIFont fontWithName:` and falls back to Menlo when registration is
absent. Staging now preserves only those font declarations while retaining all
CodexPad scene and networking metadata. This defect does not account for 100 MB
of missing resources, nor does it prove the previously blank Terminal symptom:
the WebKit renderer loads its fonts through CSS.

The old IPA gate accepted a ZIP containing only a matching runtime. It now also
requires the arm64 native Mach-O executable, metadata, compiled UI/recovery
storyboards, asset catalog, Settings, active xterm frontend/vendor scripts,
registered font files, matching protocol schema and upstream pins. It reports
IPA bytes, uncompressed app ZIP bytes, runtime bytes and the ten largest app
files. App ZIP bytes include the compressed tar as a file; expanded guest bytes
are a separate measurement. The runtime gate now checks required tools and
guest-relative link targets, Python stdlib and default OpenRC startup as well.
Regression tests reject each omitted native resource, lost font registration,
wrong native/guest architecture, stale pins/schema, missing guest tools, broken
shell links and absent service startup. No runtime is duplicated or padded.

## Verification and remaining device work

All 54 Python packaging/staging tests, five halfword tests and two PMULL tests
passed locally. The real staged target test verifies the preserved font declarations
and resource membership. Swift, Combine, Xcode/macOS and an iPad are unavailable;
the exact Codemagic Swift model regression, Xcode build and UI execution were
not run. Neither GitHub Actions nor Codemagic was started; caches were retained.

Once the IPA is downloadable, run the IPA verifier against its verified runtime
input and inspect its measurements. On an iPad, preserve existing app data and
credentials: launch, open Terminal and confirm shell output plus font previews,
run `/usr/local/libexec/codexpad/diagnose`, then reconnect and attempt sign-in.
Record only the diagnostic JSON phase/status/boolean fields and redacted native
runtime log. Confirm OAuth callback and completed account state on device; do
not share raw server logs, tokens, account data or authorization URLs. Existing
emulator limitations and prior Linux probes are described in
`sign-in-pmull-repair.md`; this packaging audit does not resolve or conceal them.
