"""Exercise the staged guest poll_wait loop under irrelevant host readiness."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
FORK = PROJECT / "upstream/ios-linuxkit"
spec = importlib.util.spec_from_file_location("staging", PROJECT / "scripts/prepare-arm64-ios.py")
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)

HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
#include "util/list.h"
typedef int lock_t;
static void lock(lock_t *p) { (void)p; }
static void unlock(lock_t *p) { (void)p; }
struct fd;
struct fd_ops { int (*poll)(struct fd *); };
struct fd { const struct fd_ops *ops; };
__POLL_HEADER__
struct sighand { lock_t lock; };
struct group {
    bool doing_group_exit;
    void *tty;
    _Atomic uint64_t last_progress_ns;
    lock_t lock;
    struct list threads;
};
struct task {
    struct group *group;
    struct sighand *sighand;
    unsigned pending, blocked;
    bool blocking, zombie;
    uint64_t last_unblocked_ns;
    int pid;
    struct list group_links, children, siblings;
};
static struct group group;
static struct task task = {.group = &group, .pid = 1};
static struct task *current = &task;
static lock_t pids_lock;
#define _EINTR (-EINTR)
#define printk(...) fprintf(stderr, __VA_ARGS__)
static int errno_map(void) { return -errno; }
static void sockrestart_begin_listen_wait(struct fd *fd) { (void)fd; }
static void sockrestart_end_listen_wait(struct fd *fd) { (void)fd; }
static bool sockrestart_should_restart_listen_wait(void) { return false; }
static _Noreturn void do_exit_group(int status) { exit(status); }
static bool poll_fd_is_real(struct poll_fd *p) { (void)p; return false; }
static int poll_real_refresh(struct poll *p, struct fd *fd) { (void)p; (void)fd; return 0; }
static void safe_close(int fd) { if (fd > 2) close(fd); }
struct real_poll_event { struct poll_fd *data; int events; };
static struct poll_fd registration;
static bool host_ready;
static unsigned host_waits;
static int real_poll_update(struct real_poll *p, int fd, int types, void *data) {
    (void)p; (void)fd; (void)types; (void)data; return 0;
}
static int real_poll_wait(struct real_poll *p, struct real_poll_event *e,
                          int max, struct timespec *timeout) {
    (void)p; (void)max;
    ++host_waits;
    if (host_ready) {
        // Darwin wakes on a nonempty regular log file, while the guest is
        // polling only for errors/hangup (Rust's standard-fd validity check).
        e[0] = (struct real_poll_event){&registration, POLL_READ};
        errno = 0;
        return 1;
    }
    assert(timeout != NULL);
    nanosleep(timeout, NULL);
    errno = 0;
    return 0;
}
static void *rpe_data(struct real_poll_event *e) { return e->data; }
static int rpe_events(struct real_poll_event *e) { return e->events; }
static int fd_ready(struct fd *fd) { (void)fd; return POLL_READ | POLL_WRITE; }
static int callback(void *context, int types, union poll_fd_info info) {
    (void)context; (void)info;
    assert(types & POLL_READ);
    return 1;
}
__POLL_WAIT__
int main(int argc, char **argv) {
    assert(argc == 2);
    struct poll p = {.notify_pipe = {-1, -1}};
    list_init(&p.poll_fds);
    const struct fd_ops ops = {.poll = fd_ready};
    struct fd fd = {.ops = &ops};
    registration = (struct poll_fd){.fd = &fd, .poll = &p,
                                    .types = POLL_ERR | POLL_HUP | POLL_NVAL};
    list_add(&p.poll_fds, &registration.fds);
    struct timespec timeout = {0, 0};
    int expected = 0;
    host_ready = true;
    if (strcmp(argv[1], "finite-masked") == 0)
        timeout.tv_nsec = 20000000;
    else if (strcmp(argv[1], "ready") == 0) {
        registration.types |= POLL_READ;
        expected = 1;
    } else if (strcmp(argv[1], "finite-idle") == 0) {
        host_ready = false;
        timeout.tv_nsec = 20000000;
    } else
        assert(strcmp(argv[1], "zero-masked") == 0);
    struct timespec before, after;
    clock_gettime(CLOCK_MONOTONIC, &before);
    assert(poll_wait(&p, callback, NULL, &timeout) == expected);
    clock_gettime(CLOCK_MONOTONIC, &after);
    double elapsed = after.tv_sec - before.tv_sec + (after.tv_nsec - before.tv_nsec) / 1e9;
    assert(elapsed < 1.0);
    if (strcmp(argv[1], "zero-masked") == 0 || expected)
        assert(host_waits == 0);
    else
        assert(elapsed >= 0.015);
    return 0;
}
'''


class PollTimeoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (FORK / "fs/poll.c").is_file():
            raise RuntimeError("Initialize the pinned ios-linuxkit submodule; poll validation cannot be skipped")
        cls.work = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.work.cleanup)
        source = (FORK / "fs/poll.c").read_text()
        if hasattr(staging, "repair_poll_timeouts"):
            source = staging.repair_poll_timeouts(source)
        start = source.index("int poll_wait(")
        end = source.index("\nvoid poll_destroy(", start)
        header = (FORK / "fs/poll.h").read_text().replace('#include "kernel/fs.h"', "")
        harness = HARNESS.replace("__POLL_HEADER__", header).replace("__POLL_WAIT__", source[start:end])
        path = Path(cls.work.name) / "poll.c"
        path.write_text(harness)
        cls.executable = path.with_suffix("")
        subprocess.run([shutil.which("cc") or "cc", "-std=gnu11", "-Wall", "-Werror",
                        "-I", str(FORK), str(path), "-o", str(cls.executable)], check=True)

    def check_case(self, case):
        subprocess.run([str(self.executable), case], check=True, timeout=3)

    def test_zero_timeout_with_masked_host_event(self):
        self.check_case("zero-masked")

    def test_finite_timeout_with_continuous_masked_host_events(self):
        self.check_case("finite-masked")

    def test_requested_readiness_is_returned_before_timeout(self):
        self.check_case("ready")

    def test_finite_timeout_without_host_events(self):
        self.check_case("finite-idle")


if __name__ == "__main__":
    unittest.main()
