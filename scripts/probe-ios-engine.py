#!/usr/bin/env python3
"""Launch the built app in a disposable iPad simulator and require engine readiness."""
import argparse
import json
from pathlib import Path
import plistlib
import subprocess
import time


def run(*args, timeout=120):
    return subprocess.run(args, check=True, text=True, capture_output=True,
                          timeout=timeout).stdout


def runtime():
    rows = json.loads(run("xcrun", "simctl", "list", "runtimes", "-j"))["runtimes"]
    available = [row for row in rows if row.get("isAvailable")
                 and ".iOS-" in row["identifier"]]
    if not available:
        raise RuntimeError("No available iOS simulator runtime; engine validation cannot be skipped")
    return max(available, key=lambda row: tuple(map(int, row["version"].split("."))))["identifier"]


def wait_for_report(path, expected_revision, timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            report = json.loads(path.read_text())
            if report.get("ready") is not True:
                raise RuntimeError("Engine failed before readiness: " + json.dumps(report, sort_keys=True))
            if report.get("runtimeRevision") != expected_revision:
                raise RuntimeError("Engine connected to a different guest runtime revision")
            if report.get("accountReadable") is not True:
                raise RuntimeError("Engine could not read the account state needed for sign-in")
            return report
        time.sleep(1)
    raise RuntimeError("App did not publish an engine readiness result before the timeout")


def probe(app, output, expected_revision):
    output.mkdir(parents=True, exist_ok=True)
    with (app / "Info.plist").open("rb") as stream:
        identifier = plistlib.load(stream)["CFBundleIdentifier"]
    device_types = json.loads(run("xcrun", "simctl", "list", "devicetypes", "-j"))["devicetypes"]
    ipads = [row for row in device_types if row["name"].startswith("iPad")]
    if not ipads:
        raise RuntimeError("No iPad simulator device type is installed")
    device_type = next((row for row in ipads if row["name"] == "iPad Pro (11-inch) (M4)"), ipads[0])
    device = run("xcrun", "simctl", "create", "CodexPad engine readiness",
                 device_type["identifier"], runtime()).strip()
    try:
        run("xcrun", "simctl", "boot", device)
        run("xcrun", "simctl", "bootstatus", device, "-b", timeout=300)
        run("xcrun", "simctl", "install", device, str(app.resolve()))
        container = None
        for launch_index in range(3):
            if container is not None:
                run("xcrun", "simctl", "terminate", device, identifier)
                # Keep the saved Linux root and log. Rust's startup check must
                # also work when stdout/stderr point at a nonempty log file.
                server_logs = list(container.rglob("app-server.log"))
                if len(server_logs) != 1:
                    raise RuntimeError("Expected one saved guest app-server log")
                if server_logs[0].stat().st_size == 0:
                    server_logs[0].write_text("CodexPad saved-root startup regression\n")
                # Remove only the report this disposable test app just wrote.
                (container / "Documents/CodexPadEngineSmoke.json").unlink(missing_ok=True)
            run("xcrun", "simctl", "launch", device, identifier, "--codexpad-engine-smoke")
            container = Path(run("xcrun", "simctl", "get_app_container",
                                 device, identifier, "data").strip())
            report = wait_for_report(container / "Documents/CodexPadEngineSmoke.json",
                                     expected_revision)
            (output / ("engine-readiness-%d.json" % (launch_index + 1))).write_text(
                json.dumps(report, indent=2) + "\n")
            print("Engine readiness launch %d: " % (launch_index + 1) +
                  json.dumps(report, sort_keys=True), flush=True)
        (output / "engine-readiness.json").write_text(json.dumps(report, indent=2) + "\n")
        print("PASS: three real iPad launches initialized Codex, verified the saved guest revision, "
              "and read account state for sign-in", flush=True)
    finally:
        # Only this newly-created disposable simulator is cleaned up.
        # No existing device, root, credential, or build cache is touched.
        try:
            logs = subprocess.run(["xcrun", "simctl", "spawn", device, "log", "show",
                                   "--style", "compact", "--last", "5m",
                                   "--predicate", 'process == "CodexPad"'],
                                  text=True, capture_output=True, timeout=60)
            (output / "simulator-engine.log").write_text(logs.stdout)
            for line in logs.stdout.splitlines():
                if any(marker in line for marker in ("booting guest init", "runtime preparation failed",
                                                     "mount aborted", "guest process pid")):
                    print(line)
        finally:
            subprocess.run(["xcrun", "simctl", "shutdown", device], capture_output=True, timeout=60)
            subprocess.run(["xcrun", "simctl", "delete", device], capture_output=True, timeout=60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    expected = json.loads((project / "Dependencies/upstreams.json").read_text())["codex"]["revision"]
    probe(args.app, args.output, expected)
