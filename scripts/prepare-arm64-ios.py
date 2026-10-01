#!/usr/bin/env python3
"""Stage the pinned ARM64 iSH iOS target with CodexPad's unchanged SwiftUI UI.

All writes go to --output. The original i686 checkout and ARM64 submodule stay
untouched; each build gets its own deterministic staging tree.
"""

import argparse
import json
import plistlib
import re
import shutil
import subprocess
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent
FORK = PROJECT / "upstream/ios-linuxkit"
SWIFT_FILES = sorted((PROJECT / "app/CodexPad").glob("*.swift"))
SCHEMA = "CodexClientRequest.schema.json"
UPSTREAMS = "upstreams.json"


def replace_one(value: str, old: str, new: str) -> str:
    if value.count(old) != 1:
        raise ValueError(f"Expected exactly one Xcode anchor: {old[:90]!r} (found {value.count(old)})")
    return value.replace(old, new, 1)


def repair_halfword_lanes(source: str) -> str:
    # A64 single-structure halfword lanes use Q:S:size[1]. size[0] is
    # zero in valid halfword encodings, so the fork aliases every odd lane.
    old = "lane = (Q << 2) | (S << 1) | (size & 1);"
    new = "lane = (Q << 2) | (S << 1) | (size >> 1);"
    count = source.count(old)
    if count != 2:
        raise ValueError(f"Expected two ARM64 halfword lane decoders (found {count})")
    return source.replace(old, new)


def repair_pmull_aliasing(source: str) -> str:
    # PMULL reads both complete source vectors before writing Vd. GHASH's
    # baseline NEON path repeatedly aliases Vd with Vn or Vm; writing a
    # halfword per iteration otherwise destroys unread source bytes.
    source = replace_one(source,
        "        uint16_t *dst = (uint16_t *)rd;\n"
        "        uint8_t *src_n = Q ? &rn[8] : rn;\n"
        "        uint8_t *src_m = Q ? &rm[8] : rm;",
        "        uint16_t result_lanes[8];\n"
        "        uint16_t *dst = result_lanes;\n"
        "        const uint8_t *src_n = Q ? &rn[8] : rn;\n"
        "        const uint8_t *src_m = Q ? &rm[8] : rm;")
    return replace_one(source,
        "            dst[i] = result;\n        }\n    } else if (size == 3) {",
        "            dst[i] = result;\n        }\n"
        "        memcpy(rd, result_lanes, sizeof(result_lanes));\n"
        "    } else if (size == 3) {")


def object_definition(project: str, object_id: str) -> re.Match:
    # Build-phase IDs first occur as references inside PBXNativeTarget.
    # Match their actual definitions, never the first textual occurrence.
    pattern = rf"^\t\t{re.escape(object_id)} /\* [^\n]* \*/ = \{{\n.*?^\t\t\}};"
    matches = list(re.finditer(pattern, project, re.M | re.S))
    if len(matches) != 1:
        raise ValueError(f"Expected one definition for Xcode object {object_id}")
    return matches[0]


def phase_files(project: str, object_id: str, phase_type: str) -> re.Match:
    definition = object_definition(project, object_id)
    if f"\t\t\tisa = {phase_type};\n" not in definition.group():
        raise ValueError(f"Xcode object {object_id} is not {phase_type}")
    files = re.search(r"\t\t\tfiles = \(\n(.*?)\t\t\t\);", definition.group(), re.S)
    if files is None:
        raise ValueError(f"Xcode phase {object_id} has no files list")
    return files


def insert_in_section(project: str, object_id: str, entry: str, phase_type: str) -> str:
    definition = object_definition(project, object_id)
    files = definition.start() + phase_files(project, object_id, phase_type).start(1)
    return project[:files] + entry + project[files:]


def stage(destination: Path) -> None:
    config = json.loads((PROJECT / "Dependencies/arm64-runtime.json").read_text())
    actual = subprocess.check_output(["git", "-C", str(FORK), "rev-parse", "HEAD"], text=True).strip()
    if actual != config["revision"]:
        raise ValueError(f"ARM64 fork revision mismatch: {actual}")
    if destination.exists():
        raise ValueError(f"Stage destination already exists: {destination}")
    if len(SWIFT_FILES) != 14:
        raise ValueError("CodexPad Swift file set changed; review the Xcode integration")

    shutil.copytree(FORK, destination, symlinks=True,
                    ignore=shutil.ignore_patterns(".git", "build-arm64-linux*", ".cache"))
    decoder = destination / "asbestos/guest-arm64/gen.c"
    decoder.write_text(repair_halfword_lanes(decoder.read_text()))
    crypto = destination / "asbestos/guest-arm64/crypto_helpers.c"
    crypto.write_text(repair_pmull_aliasing(crypto.read_text()))
    # This pinned fork uses dispatch_once in a C translation unit without
    # importing libdispatch or enabling Clang blocks. Keep its cached Mach
    # send right, using the pthread API already imported by the file.
    darwin_path = destination / "platform/darwin.c"
    darwin = replace_one(darwin_path.read_text(),
        "static mach_port_t cached_host_self(void) {\n"
        "    static mach_port_t host = MACH_PORT_NULL;\n"
        "    static dispatch_once_t once;\n"
        "    dispatch_once(&once, ^{\n"
        "        host = mach_host_self();\n"
        "    });\n"
        "    return host;\n}",
        "static mach_port_t cached_host = MACH_PORT_NULL;\n"
        "static pthread_once_t cached_host_once = PTHREAD_ONCE_INIT;\n"
        "static void initialize_cached_host(void) {\n"
        "    cached_host = mach_host_self();\n}\n"
        "static mach_port_t cached_host_self(void) {\n"
        "    pthread_once(&cached_host_once, initialize_cached_host);\n"
        "    return cached_host;\n}")
    darwin_path.write_text(darwin)
    app = destination / "app"
    shutil.copy2(PROJECT / "scripts/CodexPadRuntimeRoot.inc", app / "CodexPadRuntimeRoot.inc")
    roots_path = app / "Roots.m"
    roots = replace_one(roots_path.read_text(), "@implementation Roots\n",
                        '#include "CodexPadRuntimeRoot.inc"\n\n@implementation Roots\n')
    roots = replace_one(roots, '[NSBundle.mainBundle URLForResource:@"root" withExtension:@"tar.gz"]',
                        '[NSBundle.mainBundle.bundleURL URLByAppendingPathComponent:@"root.tar.gz"]')
    roots_path.write_text(roots)
    roots_header = app / "Roots.h"
    roots_header.write_text(replace_one(roots_header.read_text(), "NS_ASSUME_NONNULL_END",
        "BOOL CodexPadPrepareRuntimeRoot(Roots *roots, NSError **error);\n\nNS_ASSUME_NONNULL_END"))
    delegate_path = app / "AppDelegate.m"
    delegate = replace_one(delegate_path.read_text(),
        "    NSURL *root = [Roots.instance rootUrl:Roots.instance.defaultRoot];",
        "    NSError *runtimeError = nil;\n"
        "    if (!CodexPadPrepareRuntimeRoot(Roots.instance, &runtimeError)) {\n"
        "        NSLog(@\"CodexPad runtime preparation failed: %@\", runtimeError);\n"
        "        return _EINVAL;\n"
        "    }\n"
        "    NSURL *root = [Roots.instance rootUrl:Roots.instance.defaultRoot];")
    delegate_path.write_text(delegate)
    shutil.copy2(PROJECT / "scripts/build-arm64-ios-runtime.sh", app / "codexpad-build-runtime.sh")
    shutil.copy2(PROJECT / "scripts/check-arm64-elf.py", app / "check-arm64-elf.py")
    shutil.copy2(PROJECT / "scripts/summarize-build-failure.py", app / "summarize-build-failure.py")
    # Overlay only Codex branding; keep the terminal's other image assets.
    for name in ("AppIcon.appiconset", "CodexMark.imageset"):
        target = app / "Assets.xcassets" / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(PROJECT / "app/Assets.xcassets" / name, target)
    shutil.copy2(PROJECT / "app/Icons/icon.png", app / "Icons/icon.png")
    icon_metadata = app / "Icons/Icons.plist"
    icons = plistlib.loads(icon_metadata.read_bytes())
    icons[""] = {"author": "OpenAI", "description": "Codex",
                 "link": "https://openai.com/codex/"}
    icon_metadata.write_bytes(plistlib.dumps(icons, sort_keys=False))
    codexpad = app / "CodexPad"
    codexpad.mkdir()
    for source in SWIFT_FILES:
        shutil.copy2(source, codexpad / source.name)
    shutil.copy2(PROJECT / "app/CodexPad" / SCHEMA, codexpad / SCHEMA)
    (destination / "Dependencies").mkdir(exist_ok=True)
    shutil.copy2(PROJECT / "Dependencies/upstreams.json", destination / "Dependencies/upstreams.json")

    # Retain the ARM64 fork's kernel, app lifecycle, terminal and extended
    # Files implementation. Preserve only the original CodexPad host hooks.
    terminal_fonts = plistlib.loads((app / "Info.plist").read_bytes())["UIAppFonts"]
    for name in ("SceneDelegate.m", "AppGroup.m", "Info.plist"):
        shutil.copy2(PROJECT / "app" / name, app / name)
    # UIFont previews need registration as well as the font resource files.
    # Keep CodexPad's scene/network metadata, retaining only the fork's fonts.
    app_info = plistlib.loads((app / "Info.plist").read_bytes())
    app_info["UIAppFonts"] = terminal_fonts
    (app / "Info.plist").write_bytes(plistlib.dumps(app_info, sort_keys=False))

    terminal = (app / "TerminalViewController.m").read_text()
    terminal = replace_one(terminal, "@implementation TerminalViewController\n",
        "@implementation TerminalViewController\n\n"
        "- (void)codexPadActivateInput {\n"
        "    [self.view layoutIfNeeded];\n"
        "    if (self.sessionTerminal == nil) {\n"
        "        if ([AppDelegate bootError] < 0) {\n"
        "            [self showMessage:@\"Guest runtime could not boot\"\n"
        "                     subtitle:[NSString stringWithFormat:@\"Error %d. Open filesystem settings to inspect the installed runtime.\", [AppDelegate bootError]]];\n"
        "            return;\n"
        "        }\n"
        "        [self startNewSession];\n"
        "    }\n"
        "    self.terminal = self.sessionTerminal;\n"
        "    [self.termView becomeFirstResponder];\n}\n\n"
        "- (void)codexPadDeactivateInput {\n    [self.termView resignFirstResponder];\n"
        "    [self.view endEditing:YES];\n}\n")
    (app / "TerminalViewController.m").write_text(terminal)

    iosfs = (app / "iOSFS.m").read_text()
    iosfs = replace_one(iosfs, 'static NSString *const kMountBookmarks = @"iOS Mount Bookmarks";',
        'static NSString *const kMountBookmarks = @"iOS Mount Bookmarks";\n'
        'static NSString *const kCodexPadMountPoint = @"/root/workspaces/codexpad-files";\n'
        'static NSString *const kCodexPadFolderDisplayName = @"CodexPadLinkedFolderDisplayName";')
    iosfs = replace_one(iosfs, "                ios_mount_bookmarks[path] = bookmark;\n                sync_bookmarks();",
        "                ios_mount_bookmarks[path] = bookmark;\n"
        "                if ([path isEqualToString:kCodexPadMountPoint])\n"
        "                    [NSUserDefaults.standardUserDefaults setObject:url.lastPathComponent forKey:kCodexPadFolderDisplayName];\n"
        "                sync_bookmarks();")
    iosfs = replace_one(iosfs, "        [ios_mount_bookmarks removeObjectForKey:path];\n        sync_bookmarks();",
        "        [ios_mount_bookmarks removeObjectForKey:path];\n"
        "        if ([path isEqualToString:kCodexPadMountPoint])\n"
        "            [NSUserDefaults.standardUserDefaults removeObjectForKey:kCodexPadFolderDisplayName];\n"
        "        sync_bookmarks();")
    (app / "iOSFS.m").write_text(iosfs)

    identifier = re.search(r"^ROOT_BUNDLE_IDENTIFIER = (\S+)$",
                           (PROJECT / "app/iSH.xcconfig").read_text(), re.M)
    if not identifier:
        raise ValueError("Cannot resolve the current CodexPad bundle identifier")
    ish_config = (app / "iSH.xcconfig").read_text()
    # A new bundle ID keeps the saved i686 filesystem and guest state intact.
    # An app update under the same ID would otherwise reuse an x86 fakefs.
    ish_config = re.sub(r"(?m)^ROOT_BUNDLE_IDENTIFIER = \S+$",
                        f"ROOT_BUNDLE_IDENTIFIER = {identifier.group(1)}.arm64", ish_config, count=1)
    (app / "iSH.xcconfig").write_text(ish_config)
    arm_config = app / "AppARM64.xcconfig"
    arm_config.write_text(arm_config.read_text() + "\n"
        "// CodexPad's native SwiftUI host in the ARM64 guest target.\n"
        "PRODUCT_NAME = CodexPad\n"
        "// Use the bundled classic-script renderer; Ghostty fetches WASM from file URLs.\n"
        "GCC_PREPROCESSOR_DEFINITIONS = $(inherited) USE_XTERM_RENDERER=1\n"
        "PRODUCT_MODULE_NAME = CodexPadApp\n"
        "SWIFT_VERSION = 5.0\n"
        "SWIFT_OBJC_INTERFACE_HEADER_NAME = CodexPadApp-Swift.h\n"
        "SWIFT_INSTALL_OBJC_HEADER = YES\n"
        "ENABLE_USER_SCRIPT_SANDBOXING = NO\n"
        "LIBRARY_SEARCH_PATHS = $(inherited) $(BUILT_PRODUCTS_DIR)\n"
        "OTHER_LDFLAGS = $(inherited) -lsqlite3\n"
        "ALWAYS_EMBED_SWIFT_STANDARD_LIBRARIES = YES\n"
        "IPHONEOS_DEPLOYMENT_TARGET = 17.0\n")

    root_script = app / "download-root.sh"
    root_script.write_text(replace_one(root_script.read_text(),
        'curl -L "https://$ROOTFS_URL" -o "$output"',
        'if [ -n "${CODEXPAD_ROOTFS_PATH:-}" ]; then\n'
        '    cp "$CODEXPAD_ROOTFS_PATH" "$output"\n'
        'else\n'
        '    curl --fail --location "https://$ROOTFS_URL" -o "$output"\n'
        'fi'))

    pbx_path = destination / "iSH.xcodeproj/project.pbxproj"
    pbx = pbx_path.read_text()
    runtime_phase = object_definition(pbx, "AA000300AF96D90D00FFB7A4")
    runtime_block = runtime_phase.group()
    script_match = re.search(r'^\t\t\tshellScript = (".*");$', runtime_block, re.M)
    if script_match is None:
        raise ValueError("Missing ARM64 Meson build phase script")
    runtime_block = replace_one(runtime_block, script_match.group(),
        "\t\t\tshellScript = " + json.dumps('set -eu\n/bin/bash "$SRCROOT/app/codexpad-build-runtime.sh"\n') + ";")
    outputs = "".join(f'\t\t\t\t"$(BUILT_PRODUCTS_DIR)/{name}",\n'
                      for name in ("libish.a", "libish_emu.a", "libfakefs.a"))
    runtime_block = replace_one(runtime_block, "\t\t\toutputPaths = (\n\t\t\t);",
                               "\t\t\toutputPaths = (\n" + outputs + "\t\t\t);")
    pbx = pbx[:runtime_phase.start()] + runtime_block + pbx[runtime_phase.end():]
    if "CE1000000000000000000001" in pbx:
        raise ValueError("Generated Xcode identifiers collide with the pinned fork")
    build = []
    refs = []
    children = []
    sources = []
    for index, source in enumerate(SWIFT_FILES, 1):
        file_id = f"CE2{index:021X}"
        build_id = f"CE1{index:021X}"
        name = source.name
        refs.append(f'\t\t{file_id} /* {name} */ = {{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = {name}; sourceTree = "<group>"; }};\n')
        build.append(f"\t\t{build_id} /* {name} in Sources */ = {{isa = PBXBuildFile; fileRef = {file_id} /* {name} */; }};\n")
        children.append(f"\t\t\t\t{file_id} /* {name} */,\n")
        sources.append(f"\t\t\t\t{build_id} /* {name} in Sources */,\n")
    for index, name in enumerate((SCHEMA, UPSTREAMS), 0x101):
        file_id = f"CE2{index:021X}"
        build_id = f"CE1{index:021X}"
        location = "SOURCE_ROOT" if name == UPSTREAMS else '"<group>"'
        path = "Dependencies/upstreams.json" if name == UPSTREAMS else name
        refs.append(f"\t\t{file_id} /* {name} */ = {{isa = PBXFileReference; lastKnownFileType = text.json; path = {path}; sourceTree = {location}; }};\n")
        build.append(f"\t\t{build_id} /* {name} in Resources */ = {{isa = PBXBuildFile; fileRef = {file_id} /* {name} */; }};\n")
        children.append(f"\t\t\t\t{file_id} /* {name} */,\n")

    pbx = replace_one(pbx, "/* End PBXBuildFile section */", "".join(build) + "/* End PBXBuildFile section */")
    pbx = replace_one(pbx, "/* End PBXFileReference section */", "".join(refs) + "/* End PBXFileReference section */")
    group_id = "CE3000000000000000000001"
    group = (f"\t\t{group_id} /* CodexPad */ = {{\n\t\t\tisa = PBXGroup;\n\t\t\tchildren = (\n"
             + "".join(children) + '\t\t\t);\n\t\t\tpath = CodexPad;\n\t\t\tsourceTree = "<group>";\n\t\t};\n')
    pbx = replace_one(pbx, "/* End PBXGroup section */", group + "/* End PBXGroup section */")
    pbx = replace_one(pbx, "\t\tBB792B521F96D90D00FFB7A4 /* app */ = {\n\t\t\tisa = PBXGroup;\n\t\t\tchildren = (\n",
        "\t\tBB792B521F96D90D00FFB7A4 /* app */ = {\n\t\t\tisa = PBXGroup;\n\t\t\tchildren = (\n"
        f"\t\t\t\t{group_id} /* CodexPad */,\n")
    source_phase = "AA0000081F96D90D00FFB7A4"
    resource_phase = "AA000010AF96D90D00FFB7A4"
    pbx = insert_in_section(pbx, source_phase, "".join(sources), "PBXSourcesBuildPhase")
    resource_ids = ("CE1000000000000000000101", "CE1000000000000000000102")
    pbx = insert_in_section(pbx, resource_phase,
        "".join(f"\t\t\t\t{ident} /* {name} in Resources */,\n"
                for ident, name in zip(resource_ids, (SCHEMA, UPSTREAMS))),
        "PBXResourcesBuildPhase")
    # Membership checks catch incorrect placement even when comments elsewhere
    # misleadingly say "in Sources", as happened in the original insertion.
    source_members = phase_files(pbx, source_phase, "PBXSourcesBuildPhase").group(1)
    resource_members = phase_files(pbx, resource_phase, "PBXResourcesBuildPhase").group(1)
    target = object_definition(pbx, "AA0000011F96D90D00FFB7A4").group()
    if source_phase not in target or resource_phase not in target:
        raise ValueError("ARM64 target does not reference the expected build phases")
    for index, source in enumerate(SWIFT_FILES, 1):
        build_id = f"CE1{index:021X}"
        if source_members.count(build_id) != 1 or build_id in resource_members:
            raise ValueError(f"Swift source is not compiled exactly once: {source.name}")
    for ident in resource_ids:
        if resource_members.count(ident) != 1 or ident in source_members:
            raise ValueError(f"JSON resource is not packaged exactly once: {ident}")
    pbx_path.write_text(pbx)

    # The selected Xcode target must remain the fork's ARM64 target, never the
    # sibling x86 project. These are source-level checks; xcodebuild is the gate.
    assert pbx.count("CodexPadHostController.swift in Sources") == 2
    assert "#import \"CodexPadApp-Swift.h\"" in (app / "SceneDelegate.m").read_text()
    assert "GUEST_ARM64=1" in (app / "GuestARM64.xcconfig").read_text()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    stage(arguments.output.resolve())
    print(f"Staged ARM64 CodexPad target: {arguments.output.resolve()}")
