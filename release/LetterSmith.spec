from __future__ import annotations

import json
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


PROJECT_ROOT = Path.cwd().resolve()
RELEASE_ROOT = PROJECT_ROOT / "release"
MANIFEST = json.loads(
    (RELEASE_ROOT / "release_manifest.json").read_text(encoding="utf-8")
)

datas = []
app_root = PROJECT_ROOT / "resources" / "app"
for relative_dir, names in MANIFEST["application_resources"].items():
    destination = str(Path("resources/app") / relative_dir)
    for name in names:
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

binaries = [
    (str(PROJECT_ROOT / item["source"]), item["destination"])
    for item in MANIFEST["binaries"]
]

a = Analysis(
    [str(PROJECT_ROOT / MANIFEST["entrypoint"])],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=MANIFEST["hidden_imports"] + collect_submodules("keyring.backends"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=MANIFEST["excluded_modules"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

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
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "resources/app/icons/folder/lsmith.ico"),
    version=str(RELEASE_ROOT / "version_info.txt"),
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
