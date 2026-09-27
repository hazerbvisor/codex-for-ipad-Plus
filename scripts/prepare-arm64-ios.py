#!/usr/bin/env python3
"""Stage the pinned ARM64 iSH iOS target with CodexPad's unchanged SwiftUI UI.

All writes go to --output. The original i686 checkout and ARM64 submodule stay
untouched; each build gets its own deterministic staging tree.
"""

import argparse
import json
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


def insert_in_section(project: str, object_id: str, entry: str) -> str:
    start = project.index(f"\t\t{object_id} /* ")
    files = project.index("\t\t\tfiles = (\n", start) + len("\t\t\tfiles = (\n")
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
    app = destination / "app"
    codexpad = app / "CodexPad"
    codexpad.mkdir()
    for source in SWIFT_FILES:
        shutil.copy2(source, codexpad / source.name)
    shutil.copy2(PROJECT / "app/CodexPad" / SCHEMA, codexpad / SCHEMA)
    (destination / "Dependencies").mkdir(exist_ok=True)
    shutil.copy2(PROJECT / "Dependencies/upstreams.json", destination / "Dependencies/upstreams.json")

    # Retain the ARM64 fork's kernel, app lifecycle, terminal and extended
    # Files implementation. Preserve only the original CodexPad host hooks.
    for name in ("SceneDelegate.m", "AppGroup.m", "Info.plist"):
        shutil.copy2(PROJECT / "app" / name, app / name)

    terminal = (app / "TerminalViewController.m").read_text()
    terminal = replace_one(terminal, "@implementation TerminalViewController\n",
        "@implementation TerminalViewController\n\n"
        "- (void)codexPadActivateInput {\n    [self.termView becomeFirstResponder];\n}\n\n"
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
        "PRODUCT_MODULE_NAME = CodexPadApp\n"
        "SWIFT_OBJC_INTERFACE_HEADER_NAME = CodexPadApp-Swift.h\n"
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
    pbx = insert_in_section(pbx, "AA0000081F96D90D00FFB7A4", "".join(sources))
    resource_ids = ("CE1000000000000000000101", "CE1000000000000000000102")
    pbx = insert_in_section(pbx, "AA000010AF96D90D00FFB7A4",
        "".join(f"\t\t\t\t{ident} /* {name} in Resources */,\n"
                for ident, name in zip(resource_ids, (SCHEMA, UPSTREAMS))))
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
