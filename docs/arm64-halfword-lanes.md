# ARM64 halfword lane repair

The pinned ios-linuxkit revision `61719f8177499f789fc2f7f421ea919ac7780608`
uses `size & 1` for halfword lane selection in both single-structure SIMD
load/store addressing forms. Valid halfword encodings have `size[0] = 0`;
the lane index is `Q:S:size[1]`. Consequently, odd lanes overwrite/read the
preceding even lane. Staging repairs both expressions to `size >> 1` in the
copied decoder. The pinned checkout, runtime certificates and service TLS
configuration remain unchanged. Staging rejects unexpected source drift.

## Regression coverage

Run `python3 scripts/tests/test_arm64_halfword_lanes.py` after initializing
the pinned submodule and its dependencies. A C compiler is required. The test
compiles the **entire actual decoder** twice: the untouched pinned source and
the source produced by full iOS staging. Gadget symbols are address-only stubs;
the harness inspects the actual generated gadget/operand stream.

The checked-in instruction fixture was assembled with Zig 0.14.1's Clang for
AArch64 Linux. Its 192 real encodings cover LD1–LD4 and ST1–ST4, all eight
halfword lanes, base addressing, immediate post-index and register post-index,
including vector-register wraparound. Every expected operand, address advance
and writeback is checked. The original decoder fails exactly the 96 odd-lane
cases; all 192 staged cases pass. Additional checks reject changed patch anchors
and verify staging leaves the submodule untouched. Codemagic runs this gate
before staging the tree used by its native Xcode build.

## Actual guest execution

`python3 scripts/tests/build_arm64_lane_guest.py --output guest-lanes.c`
emits a standalone C fixture using the same 192 instruction words. It executes
each word with real vector registers and checks guest memory, every vector
lane, multi-register transfers, and base-register writeback. The generated
program was compiled as a static AArch64 musl executable with Zig 0.14.1.

On 2026-09-30 it reported `cases=192 failures=0` under QEMU directly and
inside the repaired ios-linuxkit Linux host running under QEMU. The same guest
executable reported `cases=192 failures=96` inside the original decoder host.
This executes the actual ARM64 JIT gadgets, beyond the operand-only regressions.

The staged Linux host's `libish.a`, `libish_emu.a` and `libfakefs.a` were built
with Meson/Ninja and Zig targeting AArch64 glibc 2.41. The Linux experiment used
local build-command adapters for Zig's assembly-to-stdout handling, disabled
sanitizers for the freestanding VDSO, and allowed the fork's date/time macros.
No runtime source changes were required. The original comparison executable
linked the untouched pinned `gen.c` object ahead of the same runtime archives.
This Linux build is not the native iOS Xcode build.

## Provider-list corruption

The official app-server pin is `66eab8ece44141ff92707868269e1d53b40c4ac5`,
release `rust-v0.155.0-alpha.4`, asset
`codex-app-server-aarch64-unknown-linux-musl.tar.gz`. The archive SHA256 is
`a7f84702edac562d5bb4827507f9658cb789e76320eefd3d142eaf7a86115f86`.

Disassembly of that verified binary contains the scheme-list SIMD copy at
`0x7b17c8c`–`0x7b17d10`: scalar halfword loads seed lane 0, LD1 halfword
loads fill lanes 1–7 in v0 and v1, and ST2 interleaves them. The regression
includes those exact SIMD instruction words, including the post-index lane-4
load. Integer address setup is omitted.

Rustls 0.23.36's aws-lc provider mapping begins with P-384, P-256, P-521,
Ed25519, RSA-PSS SHA512/SHA384/SHA256, and RSA-PKCS1 SHA512. P-521 occupies
lane 2. The test represents schemes by TLS wire IDs, replays actual decoder
operands, and **models** halfword writes and ST2 interleaving. Before repair,
lane 3 overwrites P-521 (`0x0603`) with Ed25519 (`0x0807`). After repair,
the original list survives. This test does not execute the JIT gadgets or
reproduce the entire Rustls panic in an iPad process.

## OAuth verification limits

On 2026-09-30 the exact checksum-verified binary was run under QEMU 10.0.11
on Linux, once with `SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt` and
once with that variable unset. Both initialized, accepted `account/login/start`
for ChatGPT without a provider panic, and handled a callback with matching
state and an intentionally invalid authorization code. Both reported:

```
Token exchange failed: token endpoint returned status 401 Unauthorized:
Could not validate your token. Please try signing in again.
```

That is an HTTP response from the token endpoint, so TLS and HTTP transport
completed in this environment with certificate verification enabled. The local
callback returned an HTTP 200 error page; that local status is not the token
endpoint's status. No real account credentials or authorization code were used.

QEMU does not use ios-linuxkit's decoder. These results do not establish that
the original iPad OAuth transport failure is fixed, that missing certificates
caused it, or that Cloudflare caused it. The repair addresses a confirmed guest
instruction-decoding defect and explains the observed Rustls provider-list
assertion. Actual iPad TLS and successful account sign-in remain to be verified.
