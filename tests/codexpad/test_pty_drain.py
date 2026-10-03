"""Run the staged tty read function on unread output after slave hangup."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_parent_death_signal import function, staging

HARNESS = r'''
#include <assert.h>
#include <string.h>
#include "kernel/calls.h"
#include "fs/tty.h"
__thread struct task *current;
lock_t pids_lock = LOCK_INITIALIZER;
struct tty_driver pty_master, pty_slave;
static bool half_closed;
static bool close_during_wait;
void notify(cond_t *cond) { (void)cond; }
int wait_for(cond_t *cond, lock_t *mutex, struct timespec *timeout) {
    (void)cond; (void)mutex; (void)timeout;
    assert(close_during_wait); half_closed = true; return 0;
}
static bool pty_is_half_closed_master(struct tty *tty) {
    return tty->driver == &pty_master && half_closed;
}
static int tty_signal_if_background(struct tty *tty, pid_t_ pgid, int sig) {
    (void)tty; (void)pgid; (void)sig; return 0;
}
__FUNCTIONS__
int main(void) {
    struct tgroup group = {.pgid = 1};
    struct task task = {.group = &group}; current = &task;
    struct tty tty = {.driver = &pty_master}; lock_init(&tty.lock);
    struct fd fd = {.tty = &tty, .flags = O_NONBLOCK_};
    char output[32];
    // A fast command writes, closes the slave, and exits before the first read.
    memcpy(tty.buf, "pwd-output\n", 11); tty.bufsize = 11; tty.hung_up = true;
    assert(tty_read(&fd, output, 4) == 4 && memcmp(output, "pwd-", 4) == 0);
    assert(tty_read(&fd, output, sizeof(output)) == 7 && memcmp(output, "output\n", 7) == 0);
    assert(tty_read(&fd, output, sizeof(output)) == _EIO);
    assert(tty_read(&fd, output, 0) == 0);
    // The slave may be half-closed even before master.hung_up is set.
    tty.hung_up = false; half_closed = true;
    memcpy(tty.buf, "tail", 4); tty.bufsize = 4;
    tty.termios.cc[VMIN_] = 10;
    assert(tty_read(&fd, output, sizeof(output)) == 4 && memcmp(output, "tail", 4) == 0);
    assert(tty_read(&fd, output, sizeof(output)) == _EIO);
    // Packet-mode output retains its status prefix while draining.
    tty.pty.packet_mode = true; memcpy(tty.buf, "pkt", 3); tty.bufsize = 3;
    assert(tty_read(&fd, output, sizeof(output)) == 4 && output[0] == 0 && memcmp(output + 1, "pkt", 3) == 0);
    tty.pty.packet_mode = false;
    // A waiting read also drains a partial VMIN buffer when the slave closes.
    half_closed = false; close_during_wait = true; fd.flags = 0;
    memcpy(tty.buf, "late", 4); tty.bufsize = 4;
    assert(tty_read(&fd, output, sizeof(output)) == 4 && memcmp(output, "late", 4) == 0);
    close_during_wait = false;
    // Open empty masters retain nonblocking behavior; hung-up slaves retain EOF.
    half_closed = false; fd.flags = O_NONBLOCK_;
    assert(tty_read(&fd, output, sizeof(output)) == _EAGAIN);
    tty.driver = &pty_slave; tty.hung_up = true;
    assert(tty_read(&fd, output, sizeof(output)) == 0);
    return 0;
}
'''


class PtyDrainTests(unittest.TestCase):
    def test_closed_slave_output_is_drained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            staging.stage(root)
            tty = (root / "fs/tty.c").read_text()
            functions = "\n".join(function(tty, signature) for signature in [
                "static void tty_read_into_buf(", "static size_t tty_canon_size(", "static ssize_t tty_read(",
            ])
            source = Path(temporary) / "tty.c"
            source.write_text(HARNESS.replace("__FUNCTIONS__", functions))
            binary = source.with_suffix("")
            subprocess.run([shutil.which("cc") or "cc", "-std=gnu11", "-DGUEST_ARM64=1",
                            "-I", str(root), str(source), "-pthread", "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True, timeout=5)


if __name__ == "__main__":
    unittest.main()
