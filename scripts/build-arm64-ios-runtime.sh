#!/usr/bin/env bash
# Called by the staged Xcode target. Build native iOS host archives, not Linux ELF.
set -euo pipefail
trap 'echo "ARM64 runtime build failed at line $LINENO; see the Meson/Ninja error above" >&2' ERR

: "${SRCROOT:?Xcode must provide SRCROOT}"
: "${MESON_BUILD_DIR:?Xcode must provide MESON_BUILD_DIR}"
: "${BUILT_PRODUCTS_DIR:?Xcode must provide BUILT_PRODUCTS_DIR}"
[[ "${ARCHS:-arm64}" == arm64 ]] || { echo 'Only the arm64 host target is supported' >&2; exit 1; }

platform="${PLATFORM_NAME:-iphoneos}"
deployment="${IPHONEOS_DEPLOYMENT_TARGET:-17.0}"
case "$platform" in
    iphoneos) target="arm64-apple-ios$deployment" ;;
    iphonesimulator) target="arm64-apple-ios$deployment-simulator" ;;
    *) echo "Unsupported iOS platform: $platform" >&2; exit 1 ;;
esac
sdk="$(xcrun --sdk "$platform" --show-sdk-path)"
compiler="$(xcrun --sdk "$platform" --find clang)"
archiver="$(xcrun --sdk "$platform" --find ar)"
native_compiler="$(xcrun --sdk macosx --find clang)"
command -v meson >/dev/null
command -v ninja >/dev/null

mkdir -p "$MESON_BUILD_DIR" "$BUILT_PRODUCTS_DIR"
build="$(cd "$MESON_BUILD_DIR" && pwd)"
products="$(cd "$BUILT_PRODUCTS_DIR" && pwd)"
cross="$build/codexpad-ios-cross.ini"
python3 - "$cross" "$compiler" "$archiver" "$target" "$sdk" <<'PY'
import pathlib, sys
path, compiler, archiver, target, sdk = sys.argv[1:]
def value(item):
    return "'" + item.replace('\\', '\\\\').replace("'", "\\'") + "'"
flags = ', '.join(value(item) for item in ('-target', target, '-isysroot', sdk))
pathlib.Path(path).write_text(
    '[binaries]\nc = ' + value(compiler) + '\nar = ' + value(archiver) + '\n'
    "[host_machine]\nsystem = 'darwin'\ncpu_family = 'aarch64'\ncpu = 'aarch64'\nendian = 'little'\n"
    '[built-in options]\nc_args = [' + flags + ']\nc_link_args = [' + flags + ']\n'
    '[properties]\nneeds_exe_wrapper = true\n')
PY

buildtype=debug
[[ "${CONFIGURATION:-Release}" != Release ]] || buildtype=debugoptimized
sanitize=none
[[ "${ENABLE_ADDRESS_SANITIZER:-NO}" != YES ]] || sanitize=address
# Host tools must use macOS, while cross arguments explicitly select the iOS SDK.
export CC_FOR_BUILD="$native_compiler"
export CC="$native_compiler"
setup_args=(setup "$build" "$SRCROOT" --cross-file "$cross"
    -Ddefault_library=static -Dguest_arch=arm64 -Dkernel=ish
    "-Dbuildtype=$buildtype" -Db_ndebug=false "-Db_sanitize=$sanitize"
    "-Dlog=${ISH_LOG:-}" "-Dlog_handler=${ISH_LOGGER:-nslog}")
if [[ -f "$build/meson-private/coredata.dat" ]]; then
    setup_args+=(--reconfigure)
fi
env -u SDKROOT -u IPHONEOS_DEPLOYMENT_TARGET meson "${setup_args[@]}"
if ninja -C "$build" libish.a libish_emu.a libfakefs.a 2>&1 | tee "$build/ninja-build.log"; then
    :
else
    pipeline_status=("${PIPESTATUS[@]}")
    status="${pipeline_status[0]}"
    [[ "$status" != 0 ]] || status="${pipeline_status[1]}"
    python3 "$SRCROOT/app/summarize-build-failure.py" "$build/ninja-build.log" || true
    exit "$status"
fi

# An empty VDSO placeholder compiles but is unusable when the guest handles signals.
python3 "$SRCROOT/app/check-arm64-elf.py" "$build/vdso/arm64/libvdso.so.elf"
for library in libish.a libish_emu.a libfakefs.a; do
    archive="$build/$library"
    test -s "$archive" || { echo "Missing or empty runtime archive: $archive" >&2; exit 1; }
    xcrun lipo -verify_arch arm64 "$archive"
done
# Verify all outputs before publishing any. Copy real archives instead of dangling links.
for library in libish.a libish_emu.a libfakefs.a; do
    rm -f "$products/$library"
    install -m 0644 "$build/$library" "$products/$library"
done
echo "Verified ARM64 iOS runtime archives in $products"
