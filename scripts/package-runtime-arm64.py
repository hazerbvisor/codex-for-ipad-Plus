#!/usr/bin/env python3
"""Build the CodexPad AArch64 Alpine rootfs on Linux or macOS without Docker."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
CONFIG = PROJECT / "Dependencies/arm64-runtime.json"
UPSTREAMS = PROJECT / "Dependencies/upstreams.json"
GUEST_PACKAGES = [
    "bash", "ca-certificates-bundle", "coreutils", "curl", "findutils", "git",
    "jq", "less", "openrc", "openssh-client", "patch", "python3", "ripgrep",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_file(source: Path, destination: Path, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(mode)


def check_elf(path: Path, require_static: bool = False) -> None:
    command = ["python3", str(PROJECT / "scripts/check-arm64-elf.py")]
    if require_static:
        command.append("--static")
    command.append(str(path))
    subprocess.run(command, check=True)


def normalized_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.pax_headers = {}
    return info


def main() -> None:
    output_env = os.environ.get("OUTPUT_ROOTFS")
    if not output_env:
        raise SystemExit("OUTPUT_ROOTFS must point to the intended .tar.gz")
    output = Path(output_env).resolve()
    config = json.loads(CONFIG.read_text())
    upstreams = json.loads(UPSTREAMS.read_text())
    release = str(config["alpineRelease"])
    target = config["codexTarget"]
    if target != "aarch64-unknown-linux-musl":
        raise SystemExit(f"unexpected Codex target: {target}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codexpad-arm64-") as temp_name:
        temp = Path(temp_name)
        base = temp / "base.tar.gz"
        root = temp / "root"
        root.mkdir()
        local_base = os.environ.get("ARM64_BASE_ROOTFS")
        if local_base:
            shutil.copyfile(local_base, base)
        else:
            request = urllib.request.Request(config["rootfsUrl"], headers={"User-Agent": "CodexPad-ARM64-CI/1"})
            with urllib.request.urlopen(request, timeout=120) as response, base.open("wb") as handle:
                shutil.copyfileobj(response, handle)
        actual = sha256(base)
        if actual != config["rootfsSha256"]:
            raise SystemExit(f"Alpine rootfs SHA-256 mismatch: {actual}")

        # The archive is pinned by SHA-256 above; preserve its trusted links and modes.
        with tarfile.open(base, "r:gz") as archive:
            try:
                archive.extractall(root, filter="fully_trusted")
            except TypeError:  # Python < 3.12
                archive.extractall(root)
        check_elf(root / "bin/busybox")

        bundle = root / "usr/local/share/codexpad/apks"
        subprocess.run([
            "python3", str(PROJECT / "scripts/resolve-alpine-apks.py"),
            "--release", release,
            "--arch", "aarch64",
            "--output", str(bundle),
            *GUEST_PACKAGES,
        ], check=True)

        copy_file(PROJECT / "runtime/usr/local/libexec/codexpad/boot",
                  root / "usr/local/libexec/codexpad/boot", 0o755)
        inittab = root / "etc/inittab"
        text = inittab.read_text()
        old = "::sysinit:/sbin/openrc sysinit"
        if text.count(old) != 1:
            raise SystemExit("unexpected Alpine inittab; refusing to alter boot semantics")
        inittab.write_text(text.replace(old, "::sysinit:/usr/local/libexec/codexpad/boot", 1))

        (root / "root/.codex").mkdir(parents=True, exist_ok=True)
        (root / "root/.codex").chmod(0o700)
        (root / "root/workspace").mkdir(parents=True, exist_ok=True)
        (root / "var/log/codexpad").mkdir(parents=True, exist_ok=True)
        copy_file(PROJECT / "THIRD_PARTY_NOTICES.md",
                  root / "usr/local/share/codexpad/THIRD_PARTY_NOTICES.md", 0o644)

        binary_env = os.environ.get("CODEX_BINARY")
        has_server = bool(binary_env)
        codex_revision = upstreams["codex"]["revision"]
        if has_server:
            source_env = os.environ.get("CODEX_SOURCE_DIR")
            if not source_env:
                raise SystemExit("CODEX_SOURCE_DIR is required when CODEX_BINARY is set")
            source = Path(source_env)
            revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
            if revision != codex_revision:
                raise SystemExit("Codex checkout does not match the pinned revision")
            if not (source / "LICENSE").is_file():
                raise SystemExit("pinned Codex checkout has no LICENSE")
            binary = Path(binary_env)
            check_elf(binary, require_static=True)
            copy_file(source / "LICENSE", root / "usr/local/share/licenses/codex/LICENSE", 0o644)
            destination = root / "usr/local/libexec/codexpad/codex-app-server"
            copy_file(binary, destination, 0o755)
            copy_file(PROJECT / "runtime/etc/init.d/codexpad", root / "etc/init.d/codexpad", 0o755)
            copy_file(PROJECT / "runtime/etc/profile.d/codexpad.sh", root / "etc/profile.d/codexpad.sh", 0o644)
            runlevel = root / "etc/runlevels/default"
            runlevel.mkdir(parents=True, exist_ok=True)
            link = runlevel / "codexpad"
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to("/etc/init.d/codexpad")
            checksum_path = root / "usr/local/share/codexpad/codex-app-server.sha256"
            checksum_path.parent.mkdir(parents=True, exist_ok=True)
            checksum_path.write_text(f"{sha256(binary)}  /usr/local/libexec/codexpad/codex-app-server\n")

        runtime = {
            "schemaVersion": 2,
            "ishRevision": config["revision"],
            "alpineRelease": release,
            "target": target,
            "hasAppServer": has_server,
            "codexRevision": codex_revision if has_server else None,
            "bundledGuestPackages": GUEST_PACKAGES,
            "guestPackageInstall": "first-boot",
        }
        runtime_path = root / "usr/local/share/codexpad/runtime.json"
        runtime_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_path.write_text(json.dumps(runtime, indent=2, sort_keys=True) + "\n")

        with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            archive.add(root, arcname=".", recursive=True, filter=normalized_filter)

    digest = sha256(output)
    Path(f"{output}.sha256").write_text(f"{digest}  {output.name}\n")
    print(f"rootfs: {output}")
    print(f"sha256: {digest}")


if __name__ == "__main__":
    main()
