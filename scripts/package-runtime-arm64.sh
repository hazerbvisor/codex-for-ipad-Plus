#!/usr/bin/env bash
# Compatibility wrapper for the cross-platform ARM64 runtime packager.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$project_root/scripts/package-runtime-arm64.py"
