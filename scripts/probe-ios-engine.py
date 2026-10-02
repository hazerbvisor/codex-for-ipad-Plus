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
    app_pid = None
    launched_at = time.time()
    try:
        run("xcrun", "simctl", "boot", device)
        run("xcrun", "simctl", "bootstatus", device, "-b", timeout=300)
        run("xcrun", "simctl", "install", device, str(app.resolve()))
        launched = run("xcrun", "simctl", "launch", device, identifier, "--codexpad-engine-smoke")
        app_pid = launched.rsplit(":", 1)[-1].strip()
        if not app_pid.isdigit():
            raise RuntimeError("Simulator launch did not return an app PID")
        container = Path(run("xcrun", "simctl", "get_app_container",
                             device, identifier, "data").strip())
        report = wait_for_report(container / "Documents/CodexPadEngineSmoke.json",
                                 expected_revision)
        (output / "engine-readiness.json").write_text(json.dumps(report, indent=2) + "\n")
        print("Engine readiness: " + json.dumps(report, sort_keys=True), flush=True)
        print("PASS: real iPad simulator app initialized Codex and verified the packaged guest revision", flush=True)
    except Exception:
        if app_pid:
            sampled = subprocess.run(["/usr/bin/sample", app_pid, "3", "1"],
                                     text=True, capture_output=True, timeout=30)
            (output / "app-thread-sample.txt").write_text(sampled.stdout + sampled.stderr)
            print("App thread sample:\n" + sampled.stdout[-30000:] + sampled.stderr, flush=True)
        crash_dir = Path.home() / "Library/Logs/DiagnosticReports"
        for crash in crash_dir.glob("CodexPad*.ips"):
            if crash.stat().st_mtime < launched_at:
                continue
            content = crash.read_text()
            decoder = json.JSONDecoder()
            _, offset = decoder.raw_decode(content)
            record = json.loads(content[offset:])
            threads = record.get("threads", [])
            selected = [thread for index, thread in enumerate(threads)
                        if index == 0 or thread.get("triggered")]
            summary = {key: record.get(key) for key in ("exception", "termination", "faultingThread")}
            summary["threads"] = selected
            (output / (crash.stem + "-summary.json")).write_text(json.dumps(summary, indent=2))
            print("App crash summary: " + json.dumps(summary), flush=True)
        raise
    finally:
        # Only this newly-created disposable simulator is cleaned up.
        # No existing device, root, credential, or build cache is touched.
        try:
            logs = subprocess.run(["xcrun", "simctl", "spawn", device, "log", "show",
                                   "--style", "compact", "--last", "5m",
                                   "--predicate", 'process == "CodexPad" OR eventMessage CONTAINS "com.joshuasyson.CodexPad.arm64"'],
                                  text=True, capture_output=True, timeout=60)
            (output / "simulator-engine.log").write_text(logs.stdout)
            for line in logs.stdout.splitlines():
                if any(marker in line for marker in ("booting guest init", "runtime preparation failed",
                                                     "mount aborted", "guest process pid", "watchdog", "crash",
                                                     "termination", "scene-create", "service exited")):
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
