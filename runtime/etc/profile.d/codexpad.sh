export CODEX_HOME="${CODEX_HOME:-/root/.codex}"

# Recovery guard: a terminal login is created for every CodexPad scene. If the
# default OpenRC runlevel did not start the app-server (or it exited during
# early boot), make one best-effort start here so the native SwiftUI client has
# something to reconnect to. This is intentionally quiet and idempotent; the
# normal OpenRC service remains the primary startup path.
if [ -x /etc/init.d/codexpad ]; then
    codexpad_pid=""
    if [ -r /run/codexpad-app-server.pid ]; then
        codexpad_pid="$(cat /run/codexpad-app-server.pid 2>/dev/null || true)"
    fi
    if [ -z "$codexpad_pid" ] || ! kill -0 "$codexpad_pid" 2>/dev/null; then
        /etc/init.d/codexpad start >/dev/null 2>&1 || true
    fi
    unset codexpad_pid
fi

cd /root/workspace 2>/dev/null || true
