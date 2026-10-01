#!/usr/bin/env python3
"""Fail CI when tracked files contain high-confidence credentials or signing material."""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

SENSITIVE_SUFFIXES = {
    ".p12", ".pfx", ".mobileprovision", ".key", ".pem"
}
SENSITIVE_NAMES = {
    "auth.json",
    "Secrets.xcconfig",
    "private.xcconfig",
    "ExportOptions.plist",
}

PATTERNS = {
    "private key": re.compile(
        r"-----BEGIN " + r"(?:(?:RSA|EC|DSA|OPENSSH) )?PRIVATE KEY-----"
    ),
    "OpenAI API key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
    ),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "credential in URL": re.compile(r"https?://[^/\\s:@]+:[^/\\s@]+@"),
}


def tracked_files() -> list[pathlib.Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [
        ROOT / path.decode("utf-8")
        for path in result.stdout.split(b"\0")
        if path
    ]


def risky_filename(path: pathlib.Path) -> bool:
    rel = path.relative_to(ROOT)
    name = rel.name
    lower = name.lower()
    if name in SENSITIVE_NAMES:
        return True
    if path.suffix.lower() in SENSITIVE_SUFFIXES:
        return True
    if lower == ".env" or (lower.startswith(".env.") and lower != ".env.example"):
        return True
    if tuple(part.lower() for part in rel.parts[-2:]) == (".codex", "auth.json"):
        return True
    return False


def main() -> int:
    findings: list[tuple[str, str, int | None]] = []

    for path in tracked_files():
        rel = str(path.relative_to(ROOT))
        if risky_filename(path):
            findings.append(("sensitive tracked file", rel, None))

        try:
            if path.stat().st_size > 2_000_000:
                continue
            data = path.read_bytes()
        except (OSError, UnicodeError):
            continue

        if b"\0" in data:
            continue

        text = data.decode("utf-8", errors="ignore")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((label, rel, line_number))

    if findings:
        print("Potential secrets detected. Values are intentionally not printed:")
        for label, path, line in findings:
            where = f"{path}:{line}" if line is not None else path
            print(f"  - {label}: {where}")
        print("\nRemove the secret from Git, rotate it if it was real, and retry.")
        return 1

    print("Secret scan passed: no high-confidence credentials or signing material found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
