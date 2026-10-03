"""Compile the staged kernel's process functions against their actual task structs."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
FORK = PROJECT / "upstream/ios-linuxkit"
PATCH = PROJECT / "scripts/patches/ios-linuxkit-parent-death-signal.patch"
spec = importlib.util.spec_from_file_location("staging", PROJECT / "scripts/prepare-arm64-ios.py")
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)


def function(source, signature):
    start = source.index(signature)
    # These kernel functions have their closing brace at column zero.
    end = source.index("\n}", start) + 2
    return source[start:end]


HARNESS = r'''
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include "kernel/calls.h"
__thread struct task *current;
static struct pid pids[MAX_PID + 1];
lock_t pids_lock = LOCK_INITIALIZER;
void cond_init(cond_t *cond) { assert(pthread_cond_init(&cond->cond, NULL) == 0); }
static unsigned char memory[8];
int user_write(addr_t addr, const void *buf, size_t size) {
    if (addr != 100 || size != 4) return 1;
    memcpy(memory, buf, size); return 0;
}
int user_read_string(addr_t addr, char *buf, size_t max) {
    (void)addr; (void)buf; (void)max; return 1;
}
static unsigned delivered;
static struct task *recipient;
static int delivered_signal;
static struct siginfo_ delivered_info;
void send_signal(struct task *task, int sig, struct siginfo_ info) {
    ++delivered; recipient = task; delivered_signal = sig; delivered_info = info;
}
__FUNCTIONS__
static struct task *process(struct task *parent) {
    struct task *task = task_create_(parent);
    assert(task);
    task->group = calloc(1, sizeof(*task->group));
    task->group->leader = task;
    task->tgid = task->pid;
    list_init(&task->group->threads);
    list_add(&task->group->threads, &task->group_links);
    return task;
}
static void set_signal(int sig) { assert(sys_prctl(1, sig, 0, 0, 0) == 0); }
int main(void) {
    struct task *init = process(NULL);
    struct task *engine = process(init);
    struct task *worker = task_create_(engine);
    worker->group = engine->group;
    worker->tgid = engine->tgid;
    list_add(&engine->group->threads, &worker->group_links);
    struct task *child = process(worker);
    current = child;
    assert(worker->pid != engine->tgid);
    assert(sys_getppid() == engine->tgid);
    current = worker;
    assert(sys_getppid() == init->tgid);
    current = init;
    assert(sys_getppid() == 0);
    current = child;
    for (int sig = 0; sig <= NUM_SIGS; ++sig) {
        set_signal(sig);
        memset(memory, 0xa5, sizeof(memory));
        assert(sys_prctl(2, 100, 0, 0, 0) == 0);
        int actual;
        memcpy(&actual, memory, sizeof(actual));
        assert(actual == sig && memory[4] == 0xa5);
    }
    assert(sys_prctl(1, NUM_SIGS + 1, 0, 0, 0) == _EINVAL);
    assert(sys_prctl(1, (addr_t)-1, 0, 0, 0) == _EINVAL);
    assert(child->parent_death_signal == NUM_SIGS);
    assert(sys_prctl(2, 0, 0, 0, 0) == _EFAULT);
    assert(sys_prctl(999, 0, 0, 0, 0) == _EINVAL);
    set_signal(SIGTERM_);
    struct task *grandchild = process(child);
    assert(grandchild->parent_death_signal == 0);
    struct task *silent = process(worker);
    worker->exiting = true;
    worker->uid = 123;
    lock(&pids_lock);
    reparent_children(worker);
    unlock(&pids_lock);
    assert(delivered == 1 && recipient == child && delivered_signal == SIGTERM_);
    assert(delivered_info.kill.pid == engine->tgid && delivered_info.kill.uid == 123);
    assert(child->parent == engine && silent->parent == engine);
    engine->exiting = true;
    lock(&pids_lock);
    reparent_children(engine);
    unlock(&pids_lock);
    assert(delivered == 2 && child->parent == init && silent->parent == init);
    current = child;
    set_signal(0);
    child->exiting = true;
    lock(&pids_lock);
    reparent_children(child);
    unlock(&pids_lock);
    assert(delivered == 2 && grandchild->parent == init);
    // Failed or unchanged credential changes retain the setting; an effective
    // UID/GID change clears it, including setresuid/setresgid and wrappers.
    child->euid = child->uid = child->suid = 100;
    set_signal(SIGTERM_);
    assert(sys_setuid(101) == _EPERM && child->parent_death_signal == SIGTERM_);
    assert(sys_setuid(100) == 0 && child->parent_death_signal == SIGTERM_);
    child->suid = 101;
    assert(sys_setuid(101) == 0 && child->parent_death_signal == 0);
    set_signal(SIGTERM_);
    assert(sys_setresuid(-1, -1, -1) == 0 && child->parent_death_signal == SIGTERM_);
    assert(sys_setresuid(-1, 100, -1) == 0 && child->parent_death_signal == 0);
    child->egid = child->gid = 100; child->sgid = 101;
    set_signal(SIGTERM_);
    assert(sys_setgid(102) == _EPERM && child->parent_death_signal == SIGTERM_);
    assert(sys_setgid(100) == 0 && child->parent_death_signal == SIGTERM_);
    assert(sys_setgid(101) == 0 && child->parent_death_signal == 0);
    set_signal(SIGTERM_);
    assert(sys_setresgid(-1, -1, -1) == 0 && child->parent_death_signal == SIGTERM_);
    assert(sys_setresgid(-1, 100, -1) == 0 && child->parent_death_signal == 0);
    return 0;
}
'''


class ParentDeathSignalTests(unittest.TestCase):
    def test_staged_kernel_process_semantics(self):
        if not (FORK / "kernel/task.c").is_file():
            self.fail("Initialize the pinned ios-linuxkit submodule; process validation cannot be skipped")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            staging.stage(root)
            functions = []
            for name, signatures in {
                "task.c": ["static bool pid_empty(", "struct pid *pid_get(",
                           "struct task *pid_get_task_zombie(", "struct task *pid_get_task(",
                           "struct task *task_create_(", "void task_clear_parent_death_signal("],
                "misc.c": ["int_t sys_prctl("],
                "getset.c": ["pid_t_ sys_getppid(", "int_t sys_setuid(", "dword_t sys_setresuid(",
                             "int_t sys_setgid(", "dword_t sys_setresgid("],
                "exit.c": ["static struct task *find_new_parent(", "static void reparent_children("],
            }.items():
                source = (root / "kernel" / name).read_text()
                if name == "misc.c":
                    functions.extend(line for line in source.splitlines() if line.startswith("#define PRCTL_"))
                functions.extend(function(source, signature) for signature in signatures)
            source = Path(temporary) / "process.c"
            source.write_text(HARNESS.replace("__FUNCTIONS__", "\n".join(functions)))
            binary = source.with_suffix("")
            subprocess.run([shutil.which("cc") or "cc", "-std=gnu11", "-DGUEST_ARM64=1",
                            "-I", str(root), str(source), "-pthread", "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True, timeout=5)

    def test_patch_rejects_changed_kernel(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(FORK / "kernel", root / "kernel")
            task = root / "kernel/task.h"
            task.write_text(task.read_text().replace("struct task *parent;", "struct task *renamed_parent;"))
            result = subprocess.run(["git", "apply", "--check", str(PATCH)], cwd=root,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
