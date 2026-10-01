# Sign-in: remaining AES-GCM corruption after the halfword repair

Investigation on 2026-10-01, based on main `ebb81d150c689f87033ea2da2648a9de96800223`
(PR #20). PRs #17–19 and their runtime-recovery/halfword/single-archive changes
are retained. No GitHub Actions or Codemagic build was requested for this fix.

## Confirmed cause

The pinned emulator's `pmull_helper` writes each 16-bit result directly into Vd
while reading successive 8-bit source lanes. With Vd equal to Vn or Vm, the
first result overwrites an unread input byte. ARM64 permits these register
aliases; the instruction must behave as if it reads its inputs before writing
its output. Staging now computes all eight result lanes in a temporary vector
and copies the completed vector to Vd. Both patch anchors are checked, and the
pinned submodule stays untouched.

AWS-LC's baseline NEON GHASH implementation uses exactly these byte PMULL
aliases. The checksum-verified official server contains, for example:

```text
0x8a9184c  pmull v16.8h, v16.8b, v3.8b
0x8a91854  pmull v0.8h, v5.8b, v0.8b
0x8a9185c  pmull v17.8h, v17.8b, v3.8b
```

An AWS-LC 1.16.2 / aws-lc-sys 0.39.0 probe (the pinned Codex dependency
versions) encrypting 16 zero bytes with an all-zero AES-256 key and nonce
produced the correct ciphertext `cea7403d4d606b6e074ec5d3baf39d18` but the
wrong GCM tag in the old guest:

```text
expected tag: d0d1c8a799996bf0265b98b5d48ab919
old guest:    e4b284a5251019a474339287f7591172
```

After repair, deterministic SHA-256/384/512, X25519, and AES-GCM results agree
with direct QEMU execution, including a 4,096-byte GCM payload with associated
data. This explains why fixing the halfword provider-list corruption in #18
still left a failure after TLS ServerHello selected AES-256-GCM.

The real guest regression executes 1,024 PMULL/PMULL2 cases across both operand
widths, both source halves, and distinct/overlapping registers. Direct QEMU and
the repaired guest report `cases=1024 failures=0`; the guest with the old PMULL
helper and **the repaired halfword decoder** reports `cases=1024 failures=192`.
The earlier 192-case halfword guest fixture still reports zero failures. Thus
these tests execute the repaired JIT, beyond checking staging strings.

## Official server comparison

The official binary is still release `rust-v0.155.0-alpha.4`, Codex revision
`66eab8ece44141ff92707868269e1d53b40c4ac5`, target
`aarch64-unknown-linux-musl`. Its SHA256 is
`0d7e590e54ea784c803ec563d5fd46a03e9fa52d4ed3ffe192c320a3e9786f9d`;
the pinned release archive SHA256 is
`a7f84702edac562d5bb4827507f9658cb789e76320eefd3d142eaf7a86115f86`.

Both hosts run that same binary inside ios-linuxkit under QEMU 10.0.11. Both
complete WebSocket initialize/initialized, runtime-manifest read, account/read
and ChatGPT account/login/start. A real local callback uses the issued state
and a deliberately invalid authorization code, without a user account:

| Host | Token exchange outcome |
| --- | --- |
| Halfword fix, original PMULL helper | Transport failure before a token HTTP response |
| Halfword fix, repaired PMULL helper | Token endpoint HTTP **401 Unauthorized** |
| Repaired helper with temporary instrumentation | HTTP 401; aliased byte PMULL execution observed |

The callback's own HTTP 200 is an error page. The **remote token endpoint's
401** establishes that verified TLS and HTTP completed. No token or valid
authorization code was supplied. The temporary instrumentation emits a fixed
instruction marker only and is absent from production staging.

The cloud environment requires a proxy. Only disposable imported test guests
received the host proxy's CA/hosts/resolver configuration and proxy routing.
The packaged Alpine CA bundle and certificate validation are unchanged.

## Failure path and evidence boundaries

| Stage | Evidence |
| --- | --- |
| Guest selection/import | Recovery selects a separately imported root when required files are absent, retaining old roots; actual pinned fakefs import of the new archive passes |
| Boot | Packaged `/sbin/init` runs the boot adapter; OpenRC reaches default runlevel in the Linux test |
| Service/process | Default-runlevel symlink starts the official loopback server; pidfile and started marker appear; `/healthz` and `/readyz` return 200 |
| WebSocket/protocol | Real official-server initialize/initialized, manifest and account RPCs pass through guest/app loopback |
| Native account/controls | Source trace: connect → initialize → revision check → engine ready → account/read; controls retain their engine-ready/pending-login gates. Native execution remains untested here |
| OAuth launch/callback | Server returns login ID/URL; native SwiftUI presents SFSafariViewController while retaining login state. Real local invalid-code callback and remote token HTTP response tested; Safari itself untested |
| Completion/credentials | Native completion notification refreshes account/models; foreground recovery reads saved state. Successful real-account login and persistence on iPad remain untested |

The Linux boot experiment needed a **local test adapter** for an existing
epoll/regular-file incompatibility encountered by BusyBox while reading OpenRC
scripts. iOS uses kqueue. That adapter is not included in this PR, and Linux
boot success does not establish native iOS boot behavior. Linux's headless init
also lacks the app's tty devices; it cannot verify Terminal rendering/input.

Two other limits were observed and are not concealed:

* A separately compiled Rustls 0.23.36 probe still reports UnexpectedEOF inside
  the guest while direct QEMU succeeds, even with matching crypto known-answer
  results and with ALPN/X25519 variants. It uses a different executable/toolchain
  from the official server. This PR does not assert universal TLS compatibility.
* Official `command/exec` rejects `/bin/true` with RPC -32603 / spawn errno 22.
  The pinned guest rejects PR_SET_PDEATHSIG, which upstream Codex requests in
  its pre-exec hook; the exact failing syscall was not traced in that command.
  This is a separate remaining process-compatibility issue;
  shell/Python subprocess tests pass, but that does not establish Codex tool
  execution. The diagnostic was instead run directly as a guest command.

## Packaging and regression checks

`verify-runtime-archive.py` now checks the actual tar's ELF architecture,
server checksum/provenance, both source pins and target, executable service
and OpenRC files, boot wiring, default runlevel, diagnostic, public CA contents
and default certificate symlink. Packaging fails if these are invalid, before
publishing the checksum. Existing fakefs-import and IPA single-archive/hash
gates remain in place.

The final local archive is **105,454,745 bytes (100.6 MiB)**, including the small
diagnostic. It imported successfully with the pinned real fakefs importer.
Shell output, Git, Python, ripgrep, filesystem read/write and 20 repeated
subprocesses pass. An init-started server also reaches the token endpoint's
HTTP 401 when the isolated guest service is given cloud proxy routing.

Available tests pass: 45 Python CodexPad tests, five halfword tests, two PMULL
tests, protocol validation (166 client methods / 11 server requests / 84
notifications / 79 schema tokens), Python compilation and shell/diff checks.
The Swift model regression adds numeric-error/privacy coverage, but Swift,
Combine, Xcode and an iPad are unavailable on this Linux host, so it was not
compiled or run. Available GitHub artifacts contain native-model logs only;
Codemagic build #18's IPA/logs were unavailable (unauthenticated artifact API
returns 401). No final signed/unsigned IPA was inspected or built here. Exactly
one bundled archive remains configured; the existing IPA gate rejects a second
copy. The approximately 109 MB IPA size is an expectation, not a measured result.

Reproduce the local gates:

```sh
python3 -m unittest discover -s tests/codexpad -p 'test_*.py'
python3 scripts/tests/test_arm64_halfword_lanes.py
python3 scripts/tests/test_arm64_pmull.py
python3 scripts/verify-runtime-archive.py /path/to/root.tar.gz
python3 scripts/tests/build_arm64_pmull_guest.py --output guest-pmull.c
# Compile guest-pmull.c as a static AArch64 Linux executable, then run it
# directly and through the staged ARM64 emulator against an isolated guest.
# With the official server running in a disposable unauthenticated guest:
python3 scripts/probe-codex-oauth.py --disposable-guest
```

## Minimum on-device verification

1. Install a future IPA built from this PR without removing the existing app
   or its saved roots. Reconnect in the native workspace. Confirm the expected
   runtime revision and `protocol.initialize.ok`; sign-in becomes available
   through the existing readiness rules. Confirm Terminal displays a prompt,
   accepts input and returns to the workspace.
2. In Terminal run `/usr/local/libexec/codexpad/diagnose`. Collect its JSON status
   lines and native **phase/code** diagnostic lines. If an older retained guest
   lacks the helper, paste/run the script there; no reset/import is needed.
   An initialized unsigned account should report authenticated=false, rather
   than a transport or protocol failure. Do not share auth files, full server
   logs, account details, or screenshots/URLs containing OAuth state or codes.
3. Complete ChatGPT sign-in through the in-app browser. Confirm
   `authentication.completed.ok` and authenticated=true, then foreground,
   reconnect and relaunch to verify credentials persist. If completion fails,
   report the last safe phase/code and whether the browser reached the local
   callback. Successful account login is the required acceptance check.

These device checks remain necessary. Compilation, a staged-source test,
or an invalid-code HTTP 401 is not successful sign-in on an iPad.
