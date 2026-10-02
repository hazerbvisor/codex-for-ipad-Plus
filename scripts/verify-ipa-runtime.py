#!/usr/bin/env python3
"""Verify the native app, Terminal resources and single verified runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import struct
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
APP = 'Payload/CodexPad.app/'
ROOT = 'Payload/CodexPad.app/root.tar.gz'
LEGACY = 'Payload/CodexPad.app/codexpad-runtime.tar.gz'
FONTS = ('JetBrainsMonoNerdFontMono-Regular.ttf', 'JetBrainsMonoNerdFontMono-Bold.ttf',
         'FiraCodeNerdFontMono-Regular.ttf', 'FiraCodeNerdFontMono-Bold.ttf')
RESOURCES = ('Assets.car', 'Settings.bundle/Root.plist', 'xterm-term.html',
             'xterm-term-bridge.js', 'term.css', 'xterm-vendor/xterm.css',
             'xterm-vendor/xterm-classic.js', 'xterm-vendor/addon-canvas.js') + FONTS


def digest(stream):
    result = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        result.update(block)
    return result.digest()


def verify(ipa, runtime):
    with zipfile.ZipFile(ipa) as archive:
        names = archive.namelist()
        if names.count(ROOT) != 1 or LEGACY in names:
            raise ValueError('IPA must bundle exactly one runtime as root.tar.gz')
        with archive.open(ROOT) as bundled, Path(runtime).open('rb') as expected:
            if digest(bundled) != digest(expected):
                raise ValueError('Bundled runtime differs from the verified build input')

        def read(path):
            if names.count(APP + path) != 1:
                raise ValueError(f'Missing or duplicate app resource: {path}')
            data = archive.read(APP + path)
            if not data:
                raise ValueError(f'Empty app resource: {path}')
            return data

        info = plistlib.loads(read('Info.plist'))
        if info.get('CFBundleExecutable') != 'CodexPad' or info.get('CFBundlePackageType') != 'APPL':
            raise ValueError('IPA metadata does not identify the native CodexPad app')
        # This workflow builds a thin arm64 executable, not an ELF guest binary.
        if names.count(APP + 'CodexPad') != 1:
            raise ValueError('IPA requires a native arm64 Mach-O executable')
        with archive.open(APP + 'CodexPad') as binary:
            header = binary.read(32)
        if (len(header) != 32 or header[:4] != b'\xcf\xfa\xed\xfe' or
                struct.unpack_from('<I', header, 4)[0] != 0x0100000c or
                struct.unpack_from('<I', header, 12)[0] != 2):
            raise ValueError('IPA requires a native arm64 Mach-O executable')
        for resource in RESOURCES:
            read(resource)
        if set(info.get('UIAppFonts', [])) != set(FONTS):
            raise ValueError('Terminal fonts are not registered in app metadata')
        if info.get('UIMainStoryboardFile') != 'Terminal' or info.get('UILaunchStoryboardName') != 'LaunchScreen':
            raise ValueError('App storyboard metadata is missing')
        for storyboard in ('Terminal', 'LaunchScreen', 'About', 'Roots'):
            # Localized storyboards compile under Base.lproj; unlocalized ones
            # (Roots in the pinned project) compile at the app bundle root.
            prefixes = tuple(APP + directory + storyboard + '.storyboardc/'
                             for directory in ('', 'Base.lproj/'))
            if not any(name.startswith(prefixes)
                       and archive.getinfo(name).file_size for name in names):
                raise ValueError(f'Missing compiled storyboard: {storyboard}')
        for bundled, source in (('upstreams.json', PROJECT / 'Dependencies/upstreams.json'),
                                ('CodexClientRequest.schema.json',
                                 PROJECT / 'app/CodexPad/CodexClientRequest.schema.json')):
            if json.loads(read(bundled)) != json.loads(source.read_bytes()):
                raise ValueError(f'Bundled protocol/pins differ: {bundled}')
        files = [entry for entry in archive.infolist() if entry.filename.startswith(APP)
                 and not entry.is_dir()]
        return dict(ipaBytes=Path(ipa).stat().st_size,
                    appBytes=sum(entry.file_size for entry in files),
                    runtimeBytes=archive.getinfo(ROOT).file_size,
                    largestFiles=[dict(path=entry.filename.removeprefix(APP), bytes=entry.file_size)
                                  for entry in sorted(files, key=lambda entry: entry.file_size,
                                                      reverse=True)[:10]])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ipa', type=Path)
    parser.add_argument('runtime', type=Path)
    args = parser.parse_args()
    report = verify(args.ipa, args.runtime)
    print('PASS: native arm64 app, UI/Terminal resources, pins and one matching runtime')
    print(json.dumps(report, indent=2))
