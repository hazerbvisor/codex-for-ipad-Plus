#!/usr/bin/env python3
"""Reject an x86 or dynamically linked Codex binary before packaging it."""

import argparse
import struct
from pathlib import Path


def check(path: Path, require_static: bool) -> None:
    data = path.read_bytes()
    if len(data) < 64 or data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError(f"{path}: expected a 64-bit little-endian ELF")
    if struct.unpack_from("<H", data, 18)[0] != 183:
        raise ValueError(f"{path}: expected AArch64 ELF (e_machine=183)")
    if require_static:
        phoff = struct.unpack_from("<Q", data, 32)[0]
        phentsize, phnum = struct.unpack_from("<HH", data, 54)
        if phentsize < 56 or phoff + phentsize * phnum > len(data):
            raise ValueError(f"{path}: invalid ELF program headers")
        for index in range(phnum):
            if struct.unpack_from("<I", data, phoff + index * phentsize)[0] == 3:
                raise ValueError(f"{path}: PT_INTERP found; expected a static musl binary")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--static", action="store_true")
    args = parser.parse_args()
    try:
        check(args.path, args.static)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
