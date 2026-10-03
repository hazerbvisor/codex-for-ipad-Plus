#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <unistd.h>
static int done[2], ready[2];
static void died(int sig) {
  assert(sig == SIGUSR1);
  assert(write(done[1], "D", 1) == 1);
  _exit(0);
}
static void *worker(void *unused) {
  (void)unused;
  pid_t parent = getpid();
  pid_t child = fork();
  assert(child >= 0);
  if (!child) {
    assert(getppid() == parent);
    assert(prctl(PR_SET_PDEATHSIG, SIGTERM) == 0);
    execl("/bin/true", "true", NULL);
    _exit(100);
  }
  int status;
  assert(waitpid(child, &status, 0) == child);
  assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
  return NULL;
}
static pid_t thread_child;
static void *parent_thread(void *unused) {
  (void)unused;
  pid_t parent = getpid();
  thread_child = fork();
  assert(thread_child >= 0);
  if (!thread_child) {
    assert(getppid() == parent);
    signal(SIGUSR1, died);
    assert(prctl(PR_SET_PDEATHSIG, SIGUSR1) == 0);
    assert(write(ready[1], "R", 1) == 1);
    for (;;)
      pause();
  }
  char r;
  assert(read(ready[0], &r, 1) == 1 && r == 'R');
  return NULL; // Only the creating thread exits; the process remains alive.
}
static void check_exec(const char *path, const char *expected) {
  pid_t child = fork();
  assert(child >= 0);
  if (!child) {
    assert(prctl(PR_SET_PDEATHSIG, SIGTERM) == 0);
    execl(path, path, "--exec-signal", expected, NULL);
    _exit(100);
  }
  int status;
  assert(waitpid(child, &status, 0) == child);
  assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
}
int main(int argc, char **argv) {
  if (argc == 3 && strcmp(argv[1], "--exec-signal") == 0) {
    int sig = -1;
    assert(prctl(PR_GET_PDEATHSIG, &sig) == 0 && sig == atoi(argv[2]));
    return 0;
  }
  check_exec("/process-regression", "15");
  check_exec("/process-regression-setuid", "0");
  pthread_t thread;
  assert(pthread_create(&thread, NULL, worker, NULL) == 0);
  assert(pthread_join(thread, NULL) == 0);
  assert(pipe(done) == 0 && pipe(ready) == 0);
  pid_t parent = fork();
  assert(parent >= 0);
  if (!parent) {
    pid_t child = fork();
    assert(child >= 0);
    if (!child) {
      int sig = 99;
      assert(prctl(PR_GET_PDEATHSIG, &sig) == 0 && sig == 0);
      signal(SIGUSR1, died);
      assert(prctl(PR_SET_PDEATHSIG, SIGUSR1) == 0);
      assert(write(ready[1], "R", 1) == 1);
      for (;;)
        pause();
    }
    char r;
    assert(read(ready[0], &r, 1) == 1 && r == 'R');
    _exit(0);
  }
  char d;
  assert(read(done[0], &d, 1) == 1 && d == 'D');
  int status;
  assert(waitpid(parent, &status, 0) == parent && WIFEXITED(status) &&
         WEXITSTATUS(status) == 0);
  assert(waitpid(-1, &status, 0) > 0 && WIFEXITED(status) &&
         WEXITSTATUS(status) == 0);
  assert(pthread_create(&thread, NULL, parent_thread, NULL) == 0);
  assert(pthread_join(thread, NULL) == 0);
  assert(read(done[0], &d, 1) == 1 && d == 'D');
  assert(waitpid(thread_child, &status, 0) == thread_child &&
         WIFEXITED(status) && WEXITSTATUS(status) == 0);
  puts("PASS worker fork/exec, fork reset, exec retention/privilege reset, "
       "parent-process and parent-thread death signals");
  return 0;
}
