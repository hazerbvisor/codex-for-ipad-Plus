#!/usr/bin/env bash
# Stage an isolated AArch64 guest image. This does not switch the iPad target.
set -euo pipefail

: "${OUTPUT_ROOTFS:?Set OUTPUT_ROOTFS to the intended .tar.gz path}"
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
config="$project_root/Dependencies/arm64-runtime.json"
release="$(jq -r '.alpineRelease' "$config")"
url="$(jq -r '.rootfsUrl' "$config")"
base_sha="$(jq -r '.rootfsSha256' "$config")"
revision="$(jq -r '.revision' "$config")"
target="$(jq -r '.codexTarget' "$config")"
codex_config="$project_root/Dependencies/upstreams.json"
codex_revision="$(jq -r '.codex.revision' "$codex_config")"
[[ "$release" == 3.24 && "$target" == aarch64-unknown-linux-musl ]]

mkdir -p "$(dirname -- "$OUTPUT_ROOTFS")"
work_dir="$(mktemp -d)"
trap 'rm -rf -- "$work_dir"' EXIT
base="$work_dir/base.tar.gz"
root="$work_dir/root"
mkdir -p "$root"
if [[ -n "${ARM64_BASE_ROOTFS:-}" ]]; then
    cp -- "$ARM64_BASE_ROOTFS" "$base"
else
    curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
        "$url" --output "$base"
fi
printf '%s  %s\n' "$base_sha" "$base" | sha256sum --check --status
tar -tzf "$base" >/dev/null
tar -xzf "$base" -C "$root"
python3 "$project_root/scripts/check-arm64-elf.py" "$root/bin/busybox"

printf '%s\n' \
    "https://dl-cdn.alpinelinux.org/alpine/v3.24/main" \
    "https://dl-cdn.alpinelinux.org/alpine/v3.24/community" \
    > "$root/etc/apk/repositories"
packages=(bash ca-certificates-bundle coreutils curl findutils git jq less openrc openssh-client patch python3 ripgrep)

if [[ -n "${APK_HOST_BINARY:-}" ]]; then
    test -x "$APK_HOST_BINARY" || { echo "APK_HOST_BINARY is not executable: $APK_HOST_BINARY" >&2; exit 1; }
    "$APK_HOST_BINARY" --version
    # apk-tools 3.x is portable to macOS. Run it as root only for package
    # ownership, disable all guest scripts/hooks, then return the staging tree
    # to the CI user. No AArch64 Linux code is executed on the macOS host.
    if [[ "$(id -u)" -eq 0 ]]; then
        "$APK_HOST_BINARY" --root "$root" --arch aarch64 --no-scripts --no-commit-hooks --no-cache add "${packages[@]}"
    else
        command -v sudo >/dev/null || { echo 'sudo is required for native apk rootfs staging' >&2; exit 1; }
        sudo -E "$APK_HOST_BINARY" --root "$root" --arch aarch64 --no-scripts --no-commit-hooks --no-cache add "${packages[@]}"
        sudo chown -R "$(id -u):$(id -g)" "$root"
    fi
else
    command -v docker >/dev/null || {
        echo 'Set APK_HOST_BINARY to a native apk-tools binary, or install Docker' >&2
        exit 1
    }
    # Linux fallback retained for local/paid builders. apk extracts packages for
    # AArch64 without QEMU/binfmt and package install scripts stay disabled.
    docker run --rm \
        --env "HOST_UID=$(id -u)" --env "HOST_GID=$(id -g)" \
        --volume "$root:/target" "alpine:$release" \
        sh -euc 'apk --root /target --arch aarch64 --no-scripts --no-cache add bash ca-certificates-bundle coreutils curl findutils git jq less openrc openssh-client patch python3 ripgrep; chown -R "$HOST_UID:$HOST_GID" /target'
fi

for executable in bin/busybox sbin/openrc usr/bin/git usr/bin/python3 usr/bin/rg; do
    test -x "$root/$executable" || { echo "Missing guest executable: $executable" >&2; exit 1; }
    python3 "$project_root/scripts/check-arm64-elf.py" "$root/$executable"
done
test -f "$root/etc/ssl/cert.pem" || test -f "$root/etc/ssl/certs/ca-certificates.crt"

# Use the already-tested CodexPad init adapter only when the service exists.
# The Alpine minirootfs may omit the inherited iSH runlevel setup.
install -D -m 0755 "$project_root/runtime/usr/local/libexec/codexpad/boot" \
    "$root/usr/local/libexec/codexpad/boot"
if grep -qx '::sysinit:/sbin/openrc sysinit' "$root/etc/inittab"; then
    sed -i 's|^::sysinit:/sbin/openrc sysinit$|::sysinit:/usr/local/libexec/codexpad/boot|' "$root/etc/inittab"
else
    echo 'Unexpected Alpine inittab; refusing to alter boot semantics' >&2
    exit 1
fi
install -d -m 0700 "$root/root/.codex"
install -d -m 0755 "$root/root/workspace" "$root/var/log/codexpad"
install -D -m 0644 "$project_root/THIRD_PARTY_NOTICES.md" \
    "$root/usr/local/share/codexpad/THIRD_PARTY_NOTICES.md"
if [[ -n "${CODEX_BINARY:-}" ]]; then
    : "${CODEX_SOURCE_DIR:?Set CODEX_SOURCE_DIR to the exact pinned Codex checkout when packaging Codex}"
    test "$(git -C "$CODEX_SOURCE_DIR" rev-parse HEAD)" = "$codex_revision"
    test -f "$CODEX_SOURCE_DIR/LICENSE"
    python3 "$project_root/scripts/check-arm64-elf.py" --static "$CODEX_BINARY"
    if [[ -f "$CODEX_BINARY.provenance.json" ]]; then
        python3 - "$codex_config" "$CODEX_BINARY" <<'VERIFY_RELEASE'
import hashlib, json, pathlib, sys
config = json.load(open(sys.argv[1]))['codex']
binary = pathlib.Path(sys.argv[2])
provenance = json.load(open(str(binary) + '.provenance.json'))
expected = {'source': 'official-release', 'repository': config['repository'],
            'revision': config['revision'], 'target': config['target'],
            'tag': config['release']['tag'], 'archiveSha256': config['release']['sha256']}
if any(provenance.get(key) != value for key, value in expected.items()):
    raise SystemExit('Official release provenance differs from the pin')
if provenance.get('binarySha256') != hashlib.sha256(binary.read_bytes()).hexdigest():
    raise SystemExit('Official release binary differs from its verified provenance')
VERIFY_RELEASE
        install -D -m 0644 "$CODEX_BINARY.provenance.json" \
            "$root/usr/local/share/codexpad/codex-app-server.provenance.json"
    fi
    install -D -m 0644 "$CODEX_SOURCE_DIR/LICENSE" \
        "$root/usr/local/share/licenses/codex/LICENSE"
    install -D -m 0755 "$CODEX_BINARY" "$root/usr/local/libexec/codexpad/codex-app-server"
    install -D -m 0755 "$project_root/runtime/etc/init.d/codexpad" "$root/etc/init.d/codexpad"
    install -D -m 0644 "$project_root/runtime/etc/profile.d/codexpad.sh" "$root/etc/profile.d/codexpad.sh"
    install -d -m 0755 "$root/etc/runlevels/default"
    ln -s /etc/init.d/codexpad "$root/etc/runlevels/default/codexpad"
    sha256sum "$CODEX_BINARY" | awk '{print $1 "  /usr/local/libexec/codexpad/codex-app-server"}' \
        > "$root/usr/local/share/codexpad/codex-app-server.sha256"
fi
install -d -m 0755 "$root/usr/local/share/codexpad"
jq -n --arg ishRevision "$revision" --arg alpineRelease "$release" \
    --arg target "$target" --arg codexRevision "$codex_revision" \
    --argjson hasAppServer "$(test -n "${CODEX_BINARY:-}" && echo true || echo false)" \
    '{schemaVersion: 1, ishRevision: $ishRevision, alpineRelease: $alpineRelease, target: $target, hasAppServer: $hasAppServer, codexRevision: (if $hasAppServer then $codexRevision else null end)}' \
    > "$root/usr/local/share/codexpad/runtime.json"
tar --numeric-owner --owner=0 --group=0 -czf "$OUTPUT_ROOTFS" -C "$root" .
sha256sum "$OUTPUT_ROOTFS" > "$OUTPUT_ROOTFS.sha256"
