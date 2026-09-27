#!/usr/bin/env bash
# Build a native macOS apk-tools binary so Codemagic's free M2 runner can
# populate an AArch64 Alpine rootfs without Docker or nested virtualization.
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
config="$project_root/Dependencies/arm64-runtime.json"
repo="$(jq -r '.apkTools.repository' "$config")"
revision="$(jq -r '.apkTools.revision' "$config")"
version="$(jq -r '.apkTools.version' "$config")"
cache_dir="${APK_TOOLS_CACHE_DIR:-$HOME/.codexpad-apk-tools}"
src="$cache_dir/src"
build="$cache_dir/build"
bin_dir="$cache_dir/bin"
apk="$bin_dir/apk"
stamp="$cache_dir/revision"

[[ "$(uname -s)" == Darwin ]] || { echo 'Native apk-tools bootstrap requires macOS' >&2; exit 1; }
[[ "$(uname -m)" == arm64 ]] || { echo 'Native apk-tools bootstrap requires Apple silicon' >&2; exit 1; }
command -v brew >/dev/null || { echo 'Homebrew is required' >&2; exit 1; }
command -v meson >/dev/null || { echo 'Meson is required' >&2; exit 1; }
command -v ninja >/dev/null || { echo 'Ninja is required' >&2; exit 1; }
command -v pkg-config >/dev/null || { echo 'pkg-config is required' >&2; exit 1; }

if [[ -x "$apk" && -f "$stamp" && "$(cat "$stamp")" == "$revision" ]] &&
   "$apk" --version 2>&1 | grep -Fq "$version"; then
    echo "Using cached native apk-tools $version"
    exit 0
fi

rm -rf -- "$src" "$build" "$bin_dir"
mkdir -p "$src" "$bin_dir"
git -C "$src" init -q
git -C "$src" remote add origin "$repo"
git -C "$src" fetch --depth 1 origin "$revision"
git -C "$src" checkout -q --detach FETCH_HEAD
test "$(git -C "$src" rev-parse HEAD)" = "$revision"

openssl_prefix="$(brew --prefix openssl@3)"
zlib_prefix="$(brew --prefix zlib)"
zstd_prefix="$(brew --prefix zstd)"
export PKG_CONFIG_PATH="$openssl_prefix/lib/pkgconfig:$zlib_prefix/lib/pkgconfig:$zstd_prefix/lib/pkgconfig:${PKG_CONFIG_PATH:-}"

meson setup "$build" "$src" \
    --auto-features=disabled \
    -Dcrypto_backend=openssl \
    -Ddocs=disabled \
    -Dhelp=disabled \
    -Dlua=disabled \
    -Dpython=disabled \
    -Dtests=disabled \
    -Durl_backend=libfetch \
    -Dzstd=enabled
ninja -C "$build" src/apk
install -m 0755 "$build/src/apk" "$apk"
printf '%s\n' "$revision" > "$stamp"
"$apk" --version
echo "Built native macOS apk-tools at $apk"
