# Codex terminal spawn repair

Opening the app's Terminal and running `pwd` can work while a model's command
fails with `Invalid argument (os error 22)`. The interactive shell and Codex's
command launcher use different process setup. This failure is independent of
the selected model.

## Confirmed failure and changes

The official pinned ARM64 Codex app-server calls
`prctl(PR_SET_PDEATHSIG, SIGTERM)` before executing a command. The pinned
`ios-linuxkit` kernel rejects that option with EINVAL. A syscall trace of the
unchanged engine reproduced `command/exec` returning RPC -32603 for `/bin/true`,
and showed the child's `prctl(1)` returning -22.

The staged kernel now supports setting and reading the parent-death signal,
validates the signal number and guest pointer, resets it on fork/clone,
delivers it when the creating task exits, and clears it on supported effective
UID/GID changes and setuid/setgid exec. Normal exec retains it. Normal and forced
thread teardown both reparent and notify children.

The same launch hook compares `getppid()` with the engine's process ID. The
kernel previously returned the creating thread's ID, which differs when a
Tokio worker forks the command. `getppid()` now uses the thread-group leader's
parent and returns the parent's process ID.

An output assertion then exposed a second defect: the PTY master returned EOF
as soon as its slave closed, even when unread command output remained. The
staged tty read path now drains those bytes and subsequently reports EIO on an
empty closed master. Slave EOF and open-master nonblocking reads retain their
existing behavior.

Both patches are applied by `prepare-arm64-ios.py` to the disposable staging
tree, with a checked application that fails if a patch no longer applies. The upstream
submodule and official engine binary remain pinned and unchanged. Codemagic
runs the process and PTY regression tests before compiling the app.

## Validation

Tested the exact pinned runtime revision
`61719f8177499f789fc2f7f421ea919ac7780608` with the existing decoder/poll repairs
and these process/PTY patches. Clang cross-compiled its ARM64 Linux CLI, and
QEMU ran that host CLI, which in turn emulated the guest. The Alpine root and
official `rust-v0.155.0-alpha.4` ARM64 engine were downloaded with the repository's
pinned checksum checks. No model API or account credentials were needed.

The official engine passes the committed `probe_terminal_spawn.py` assertions:

- `/bin/true` launches and exits zero.
- `/bin/sh` runs `pwd` in `/root/workspace`, returns stdout and stderr separately,
  and preserves exit status 7.
- PTY `pwd` exits zero and streams the directory through `command/exec/outputDelta`.

All 83 CodexPad Python regression tests, five halfword decoder tests, and two
PMULL tests pass. Both staged ARM64 runtime builds compile successfully.

The native process harness compiles the actual staged kernel functions against
the actual task structures. It covers worker-thread parent IDs, signal numbers
0–64, invalid input, the four-byte getter, fork reset, reparenting, signal
notification, and credential resets. The tty harness compiles the actual staged
read function and covers buffered hangup output, partial reads, packet mode,
partial VMIN reads, empty-master EIO, open-master EAGAIN, and slave EOF.

The static ARM64 guest fixture also passes worker fork/exec, real signal delivery
on parent-process and creating-thread death, fork reset, ordinary exec retention,
and setuid exec reset. To reproduce it in a disposable root with an ARM64 CLI:

```sh
aarch64-linux-gnu-gcc -static -pthread \
  tests/codexpad/parent_death_signal_guest.c -o /tmp/process-regression
cp /tmp/process-regression "$GUEST_ROOT/process-regression"
cp /tmp/process-regression "$GUEST_ROOT/process-regression-setuid"
chmod 4755 "$GUEST_ROOT/process-regression-setuid"
timeout 30 qemu-aarch64-static "$ISH_CLI" -r "$GUEST_ROOT" /process-regression
python3 tests/codexpad/probe_terminal_spawn.py \
  --runtime "$ISH_CLI" --root "$GUEST_ROOT" \
  --emulator qemu-aarch64-static --log /tmp/codexpad-terminal.log
```

Provision that root with the checksum-verified engine at `/codex-app-server`,
`/bin/sh`, `/bin/true`, `/root/workspace`, and the runtime's `/dev/ptmx` and
`/dev/pts`. For realfs Linux CLI tests, `/dev/ptmx` must be a placeholder regular
file, which the runtime reclassifies as its guest device; opening a host device
node from an unmounted directory does not create the guest PTY. The packaged
app uses fakefs device metadata. Native ARM64 hosts can omit `--emulator`.

One earlier QEMU host run terminated with SIGBUS during engine initialization;
the clean-root final engine probe passed. These checks establish the guest
command paths in the Linux CLI, not native iOS execution or full engine stability.
Xcode/Codemagic must rebuild the app, and the resulting IPA still needs an iPad
smoke test: open Terminal, send a model `Run pwd`, verify the command result,
and repeat with another model. No rebuilt IPA was produced in this workspace.
