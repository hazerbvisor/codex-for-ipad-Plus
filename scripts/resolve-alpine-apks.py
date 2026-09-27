#!/usr/bin/env python3
"""Resolve Alpine v2 repository dependencies and download .apk files without executing them.

This is intended for cross-platform CI hosts (notably macOS). The downloaded
AArch64 packages are installed later *inside* the ARM64 guest by Alpine's own
apk binary, so the host never needs Docker, QEMU, or a foreign-architecture
package manager.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import tarfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MIRROR = "https://dl-cdn.alpinelinux.org/alpine"
REPOSITORIES = ("main", "community")
DEP_NAME = re.compile(r"^([^<>=~]+)")


@dataclass(frozen=True)
class Package:
    repo: str
    name: str
    version: str
    arch: str
    depends: tuple[str, ...]
    provides: tuple[str, ...]
    size: int | None

    @property
    def filename(self) -> str:
        return f"{self.name}-{self.version}.apk"


def fetch(url: str, attempts: int = 4) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "CodexPad-ARM64-CI/1"})
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            last = error
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"failed to download {url}: {last}")


def apkindex_text(raw: bytes) -> str:
    # APKINDEX.tar.gz uses the APK v2 concatenated-gzip/tar layout. Python's
    # gzip reader consumes concatenated gzip members, leaving one tar stream.
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
        tar_bytes = compressed.read()
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as archive:
        for member in archive.getmembers():
            if member.name.rsplit("/", 1)[-1] == "APKINDEX":
                handle = archive.extractfile(member)
                if handle is None:
                    break
                return handle.read().decode("utf-8")
    raise ValueError("APKINDEX member not found")


def parse_index(repo: str, text: str, arch: str) -> list[Package]:
    packages: list[Package] = []
    for block in text.strip().split("\n\n"):
        fields: dict[str, list[str]] = {}
        for line in block.splitlines():
            if len(line) < 2 or line[1] != ":":
                continue
            fields.setdefault(line[0], []).append(line[2:])
        if not fields.get("P") or not fields.get("V"):
            continue
        pkg_arch = fields.get("A", [arch])[0]
        if pkg_arch not in (arch, "noarch"):
            continue
        depends = tuple(fields.get("D", [""])[0].split())
        provides = tuple(fields.get("p", [""])[0].split())
        size_text = fields.get("S", [""])[0]
        packages.append(Package(
            repo=repo,
            name=fields["P"][0],
            version=fields["V"][0],
            arch=pkg_arch,
            depends=depends,
            provides=provides,
            size=int(size_text) if size_text.isdigit() else None,
        ))
    return packages


def dep_key(expression: str) -> str | None:
    expression = expression.strip()
    if not expression or expression.startswith("!"):
        return None
    # Repository tags may appear as name@tag; the repository set here is pinned
    # already, so the tag does not alter provider selection.
    match = DEP_NAME.match(expression)
    if not match:
        return None
    return match.group(1).split("@", 1)[0]


def provide_key(expression: str) -> str:
    match = DEP_NAME.match(expression)
    return match.group(1) if match else expression


def resolve(packages: list[Package], roots: list[str]) -> list[Package]:
    by_name: dict[str, Package] = {}
    providers: dict[str, list[Package]] = {}
    for package in packages:
        by_name.setdefault(package.name, package)
        providers.setdefault(package.name, []).append(package)
        for provided in package.provides:
            providers.setdefault(provide_key(provided), []).append(package)

    selected: dict[str, Package] = {}
    queue = list(roots)
    while queue:
        expression = queue.pop(0)
        key = dep_key(expression)
        if key is None:
            continue
        if key in by_name:
            package = by_name[key]
        else:
            candidates = providers.get(key, [])
            if not candidates:
                raise RuntimeError(f"no Alpine provider found for dependency {expression!r}")
            # Prefer main over community, then stable deterministic ordering.
            package = sorted(candidates, key=lambda p: (REPOSITORIES.index(p.repo), p.name, p.version))[0]
        if package.name in selected:
            continue
        selected[package.name] = package
        queue.extend(package.depends)
    return sorted(selected.values(), key=lambda p: (REPOSITORIES.index(p.repo), p.name))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--arch", default="aarch64")
    parser.add_argument("--mirror", default=DEFAULT_MIRROR)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("packages", nargs="+")
    args = parser.parse_args()

    base = f"{args.mirror.rstrip('/')}/v{args.release}"
    all_packages: list[Package] = []
    for repo in REPOSITORIES:
        url = f"{base}/{repo}/{args.arch}/APKINDEX.tar.gz"
        raw = fetch(url)
        all_packages.extend(parse_index(repo, apkindex_text(raw), args.arch))

    resolved = resolve(all_packages, args.packages)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    for package in resolved:
        url = f"{base}/{package.repo}/{args.arch}/{package.filename}"
        data = fetch(url)
        if package.size is not None and len(data) != package.size:
            raise RuntimeError(
                f"size mismatch for {package.filename}: index={package.size}, downloaded={len(data)}"
            )
        digest = hashlib.sha256(data).hexdigest()
        destination = args.output / package.filename
        destination.write_bytes(data)
        manifest.append({
            "name": package.name,
            "version": package.version,
            "repository": package.repo,
            "filename": package.filename,
            "sha256": digest,
            "size": len(data),
        })
        print(f"bundled {package.repo}/{package.filename} {digest}")

    checksums = "".join(f"{item['sha256']}  {item['filename']}\n" for item in manifest)
    (args.output / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    (args.output / "manifest.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "release": args.release,
            "arch": args.arch,
            "roots": args.packages,
            "packages": manifest,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"resolved {len(resolved)} Alpine packages into {args.output}")


if __name__ == "__main__":
    main()
