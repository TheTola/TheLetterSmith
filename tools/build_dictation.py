"""Build the pinned Windows speech bridge. Compiler is a developer-only download.

Run: py -3.13 tools/build_dictation.py
The installer includes build/dictation-runtime; end users download only models.
"""
from __future__ import annotations
import hashlib
import json
import argparse
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build/dictation-build"
RUNTIME = ROOT / "build/dictation-runtime"
ASSETS = {
    "runtime": ("https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-x64.zip", "6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb"),
    "zig": ("https://ziglang.org/download/0.14.1/zig-x86_64-windows-0.14.1.zip", "554f5378228923ffd558eac35e21af020c73789d87afeabf4bfd16f2e6feed2c"),
}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify an existing build without downloading or compiling")
    args = parser.parse_args()
    if args.check:
        manifest = json.loads((ROOT / "dictation_runtime_hashes.json").read_text())
        metadata = json.loads((RUNTIME / "build-info.json").read_text())
        if metadata["source_sha256"] != digest(ROOT / "tools/dictation_native.cpp"):
            raise ValueError("Speech helper source changed. Rebuild it before packaging.")
        for name, expected in manifest.items():
            if digest(RUNTIME / name) != expected:
                raise ValueError("Speech runtime hash mismatch: " + name)
        if any(p.suffix.lower() in {".exe", ".dll"} and p.name not in manifest for p in RUNTIME.iterdir()):
            raise ValueError("Unexpected executable in speech runtime")
        print("Speech runtime build verified")
        return
    BUILD.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in ASSETS.items():
        archive = BUILD / (name + ".zip")
        if not archive.exists():
            urllib.request.urlretrieve(url, archive)
        if digest(archive) != expected:
            raise ValueError("Download hash mismatch: " + name)
        target = BUILD / name
        with zipfile.ZipFile(archive) as z:
            if any(not (target / i.filename).resolve().is_relative_to(target.resolve()) for i in z.infolist()):
                raise ValueError("Unsafe archive path")
            z.extractall(target)
    sources = json.loads((ROOT / "tools/dictation_headers.json").read_text())
    for name, expected in sources["files"].items():
        path = BUILD / name
        directory = "include" if name == "whisper.h" else "ggml/include"
        if not path.exists():
            urllib.request.urlretrieve(f"https://raw.githubusercontent.com/ggml-org/whisper.cpp/{sources['revision']}/{directory}/{name}", path)
        if digest(path) != expected:
            raise ValueError("Header hash mismatch: " + name)
    dlls = [p for p in (BUILD / "runtime/Release").glob("*.dll") if p.name.startswith("ggml") or p.name == "whisper.dll"]
    for path in dlls:
        shutil.copyfile(path, RUNTIME / path.name)
    for name, info in sources.get("licenses", {}).items():
        path = RUNTIME / name
        if not path.exists():
            urllib.request.urlretrieve(f"https://raw.githubusercontent.com/ggml-org/whisper.cpp/{sources['revision']}/{info['path']}", path)
        if digest(path) != info["sha256"]:
            raise ValueError("License hash mismatch: " + name)
    compiler = BUILD / "zig/zig-x86_64-windows-0.14.1/zig.exe"
    subprocess.run([str(compiler), "c++", "-std=c++17", "-O2", "-target", "x86_64-windows-gnu", "-municode",
                    "-I", str(BUILD), str(ROOT / "tools/dictation_native.cpp"), "-o", str(RUNTIME / "dictation.exe")], check=True)
    expected_names = {p.name for p in dlls} | {"dictation.exe"}
    if any(p.suffix.lower() in {".exe", ".dll"} and p.name not in expected_names for p in RUNTIME.iterdir()):
        raise ValueError("Unexpected executable in speech runtime; inspect the build directory.")
    manifest = {name: digest(RUNTIME / name) for name in sorted(expected_names)}
    (ROOT / "dictation_runtime_hashes.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (RUNTIME / "build-info.json").write_text(json.dumps({"source_sha256": digest(ROOT / "tools/dictation_native.cpp"),
                                                       "headers_revision": sources["revision"], "runtime_release": "b5454"}, indent=2) + "\n")
    print("Verified speech runtime ready:", RUNTIME)


if __name__ == "__main__":
    main()
