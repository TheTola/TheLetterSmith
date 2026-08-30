from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from button_artwork import application_resource_names


PROJECT_ROOT = Path.cwd().resolve()
RELEASE_ROOT = PROJECT_ROOT / "release"
MANIFEST = json.loads(
    (RELEASE_ROOT / "release_manifest.json").read_text(encoding="utf-8")
)
IS_MACOS = sys.platform == "darwin"
MACOS = MANIFEST["macos"]

datas = []
app_relative_root = Path(MANIFEST["application_resources_root"])
app_root = PROJECT_ROOT / app_relative_root
for relative_dir, names in MANIFEST["application_resources"].items():
    destination = str(app_relative_root / relative_dir)
    for name in application_resource_names(app_root, relative_dir, names):
        datas.append((str(app_root / relative_dir / name), destination))

prompt_root = PROJECT_ROOT / "resources" / "prompt_writer"
for name in MANIFEST["prompt_writer_files"]:
    datas.append(
        (str(prompt_root / name), "resources/prompt_writer")
    )

for item in MANIFEST["data_directories"] + MANIFEST["data_files"]:
    datas.append(
        (str(PROJECT_ROOT / item["source"]), item["destination"])
    )

datas += collect_data_files("spellchecker")

binaries_config = MACOS["binaries"] if IS_MACOS else MANIFEST["binaries"]
binaries = [
    (str(PROJECT_ROOT / item["source"]), item["destination"])
    for item in binaries_config
]

a = Analysis(
    [str(PROJECT_ROOT / MANIFEST["entrypoint"])],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=MANIFEST["hidden_imports"] + collect_submodules("keyring.backends"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(RELEASE_ROOT / "qt_runtime_hook.py")],
    excludes=MANIFEST["excluded_modules"],
    noarchive=False,
    optimize=1,
)

# PyInstaller searches the inherited PATH for dependent DLLs. Apply the
# distribution denylist here so unrelated native runtimes cannot shadow the
# Windows DLLs that Qt expects at startup.
forbidden_binary_names = {
    name.casefold()
    for name in MANIFEST["release_sanitation"]["distribution_forbidden_file_names"]
}
if not IS_MACOS:
    a.binaries = [
        entry
        for entry in a.binaries
        if Path(entry[0]).name.casefold() not in forbidden_binary_names
    ]
pyz = PYZ(a.pure)

codesign_identity = (
    os.environ.get("LETTER_SMITH_MACOS_CODESIGN_IDENTITY", "").strip() or None
    if IS_MACOS
    else None
)
target_arch = (
    os.environ.get("LETTER_SMITH_MACOS_TARGET_ARCH", "").strip() or None
    if IS_MACOS
    else None
)
entitlements_file = (
    str(RELEASE_ROOT / "macos_entitlements.plist")
    if IS_MACOS
    else None
)
app_icon = (
    PROJECT_ROOT / MACOS["icon"]
    if IS_MACOS
    else PROJECT_ROOT / "gallery/app/icons/folder/lsmith.ico"
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LetterSmith",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=target_arch,
    codesign_identity=codesign_identity,
    entitlements_file=entitlements_file,
    icon=str(app_icon),
    version=None if IS_MACOS else str(RELEASE_ROOT / "version_info.txt"),
    uac_admin=False,
    uac_uiaccess=False,
    contents_directory="_internal",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LetterSmith",
)

if IS_MACOS:
    app = BUNDLE(
        coll,
        name=MACOS["app_name"],
        icon=str(app_icon),
        bundle_identifier=MACOS["bundle_identifier"],
        version=MANIFEST["product"]["version"],
        info_plist={
            "CFBundleVersion": MANIFEST["product"]["version"],
            "CFBundleDisplayName": MANIFEST["product"]["name"],
            "CFBundleName": MANIFEST["product"]["name"],
            "LSMinimumSystemVersion": MACOS["minimum_system_version"],
            "NSHighResolutionCapable": True,
            "NSSupportsAutomaticGraphicsSwitching": True,
        },
    )
