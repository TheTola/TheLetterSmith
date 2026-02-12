#!/usr/bin/env python3
"""
Patch old Play builds to the NEW normalized runtime layout.

Handles Windows file locks:
- Never crashes on WinError 32.
- Skips locked files and continues.
- Prints a per-build report including locked skips.

Run:
  python patch_old_play_builds.py
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent
PLAY_ROOT = PROJECT_ROOT / "output" / "Play"

# Canonical source tree (refill source of truth)
SRC_PAGES = PROJECT_ROOT / "gallery" / "user" / "pages"
SRC_CONTROLS = PROJECT_ROOT / "gallery" / "user" / "card" / "controls"
SRC_SOUNDS = PROJECT_ROOT / "gallery" / "user" / "sounds"
SRC_MESSAGE = PROJECT_ROOT / "gallery" / "user" / "message"

REQUIRED_PAGES = ["cover.png", "letter.png", "wall.png", "back.png"]
REQUIRED_CONTROLS = [
    "npage.png",
    "ppage.png",
    "cleft.png",
    "cright.png",
    "volon.png",
    "voloff.png",
    "showmessageicon.png",
]
REQUIRED_SOUNDS = ["music.mp3", "glissando.mp3"] + [f"flip{i}.mp3" for i in range(1, 11)]
MESSAGE_FILES = ["message.html", "message.png"]  # png optional

# Path rewrites to normalized runtime layout
REWRITES: List[Tuple[str, str]] = [
    # pages
    (r"gallery/user/pages/", "gallery/pages/"),
    (r"gallery/(cover|letter|wall|back)\.png", r"gallery/pages/\1.png"),
    # controls/icons
    (r"gallery/user/card/controls/", "gallery/controls/"),
    (r"gallery/icons/", "gallery/controls/"),
    # sounds
    (r"gallery/user/sounds/", "gallery/sounds/"),
    # message
    (r"gallery/user/message/", "gallery/message/"),
    # normalize any accidental backslashes in quoted paths
    (r"gallery\\pages\\", "gallery/pages/"),
    (r"gallery\\controls\\", "gallery/controls/"),
    (r"gallery\\sounds\\", "gallery/sounds/"),
    (r"gallery\\message\\", "gallery/message/"),
]


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _copy_if_exists(src: Path, dst: Path, *, overwrite: bool = True) -> Tuple[bool, bool]:
    """
    Returns (copied, locked).
    - copied=True if we successfully wrote dst.
    - locked=True if dst could not be written due to WinError 32 / permission lock.
    """
    if not src.is_file():
        return (False, False)

    _ensure_dir(dst.parent)

    if dst.exists() and not overwrite:
        return (False, False)

    try:
        # Attempt overwrite
        shutil.copy2(src, dst)
        return (True, False)
    except PermissionError:
        # Usually WinError 32: locked by another process
        return (False, True)
    except OSError as e:
        # Treat WinError 32 as locked
        if getattr(e, "winerror", None) == 32:
            return (False, True)
        return (False, False)


def _first_existing(candidates: List[Path]) -> List[Path]:
    return [c for c in candidates if c.exists()]


def _copy_pages_from_build(gallery: Path, pages_dst: Path) -> Tuple[int, int]:
    copied = 0
    locked = 0
    candidates = [
        gallery,  # flat legacy: gallery/cover.png
        gallery / "user" / "pages",
        gallery / "pages",
    ]
    for src_dir in _first_existing(candidates):
        for name in REQUIRED_PAGES:
            if src_dir == gallery:
                ok, lk = _copy_if_exists(gallery / name, pages_dst / name, overwrite=True)
            else:
                ok, lk = _copy_if_exists(src_dir / name, pages_dst / name, overwrite=True)
            copied += int(ok)
            locked += int(lk)
    return copied, locked


def _copy_controls_from_build(gallery: Path, controls_dst: Path) -> Tuple[int, int]:
    copied = 0
    locked = 0
    candidates = [
        gallery / "icons",
        gallery / "user" / "card" / "controls",
        gallery / "controls",
    ]
    for src_dir in _first_existing(candidates):
        for name in REQUIRED_CONTROLS:
            ok, lk = _copy_if_exists(src_dir / name, controls_dst / name, overwrite=True)
            copied += int(ok)
            locked += int(lk)
    return copied, locked


def _copy_sounds_from_build(gallery: Path, sounds_dst: Path) -> Tuple[int, int]:
    copied = 0
    locked = 0
    candidates = [
        gallery / "sounds",
        gallery / "user" / "sounds",
    ]
    for src_dir in _first_existing(candidates):
        for name in REQUIRED_SOUNDS:
            ok, lk = _copy_if_exists(src_dir / name, sounds_dst / name, overwrite=True)
            copied += int(ok)
            locked += int(lk)
    return copied, locked


def _copy_message_from_build(gallery: Path, message_dst: Path) -> Tuple[int, int]:
    copied = 0
    locked = 0
    candidates = [
        gallery / "message",
        gallery / "user" / "message",
    ]
    for src_dir in _first_existing(candidates):
        for name in MESSAGE_FILES:
            ok, lk = _copy_if_exists(src_dir / name, message_dst / name, overwrite=True)
            copied += int(ok)
            locked += int(lk)
    return copied, locked


def _refill_missing_from_canonical(dst_dir: Path, src_dir: Path, names: List[str]) -> Tuple[int, int]:
    copied = 0
    locked = 0
    for name in names:
        dst = dst_dir / name
        if dst.is_file():
            continue
        ok, lk = _copy_if_exists(src_dir / name, dst, overwrite=True)
        copied += int(ok)
        locked += int(lk)
    return copied, locked


def _rewrite_paths(file_path: Path) -> Tuple[bool, bool]:
    """
    Returns (rewrote, locked).
    """
    if not file_path.is_file():
        return (False, False)

    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return (False, False)

    original = text
    for pat, rep in REWRITES:
        text = re.sub(pat, rep, text)

    if text == original:
        return (False, False)

    try:
        file_path.write_text(text, encoding="utf-8")
        return (True, False)
    except PermissionError:
        return (False, True)
    except OSError as e:
        if getattr(e, "winerror", None) == 32:
            return (False, True)
        return (False, False)


def patch_one_build(build_dir: Path) -> Dict:
    report = {
        "build": build_dir.name,
        "gallery_found": False,
        "copied_from_build": 0,
        "refilled_from_canonical": 0,
        "locked_skips": 0,
        "rewrote_index": False,
        "rewrote_script": False,
        "rewrote_css": False,
        "rewrote_locked": 0,
        "missing_after": [],
    }

    gallery = build_dir / "gallery"
    if not gallery.is_dir():
        return report

    report["gallery_found"] = True

    pages_dst = gallery / "pages"
    controls_dst = gallery / "controls"
    sounds_dst = gallery / "sounds"
    message_dst = gallery / "message"

    for d in (pages_dst, controls_dst, sounds_dst, message_dst):
        _ensure_dir(d)

    # Copy what build already has
    c, l = _copy_pages_from_build(gallery, pages_dst)
    report["copied_from_build"] += c
    report["locked_skips"] += l

    c, l = _copy_controls_from_build(gallery, controls_dst)
    report["copied_from_build"] += c
    report["locked_skips"] += l

    c, l = _copy_sounds_from_build(gallery, sounds_dst)
    report["copied_from_build"] += c
    report["locked_skips"] += l

    c, l = _copy_message_from_build(gallery, message_dst)
    report["copied_from_build"] += c
    report["locked_skips"] += l

    # Refill missing from canonical source tree
    c, l = _refill_missing_from_canonical(pages_dst, SRC_PAGES, REQUIRED_PAGES)
    report["refilled_from_canonical"] += c
    report["locked_skips"] += l

    c, l = _refill_missing_from_canonical(controls_dst, SRC_CONTROLS, REQUIRED_CONTROLS)
    report["refilled_from_canonical"] += c
    report["locked_skips"] += l

    c, l = _refill_missing_from_canonical(sounds_dst, SRC_SOUNDS, REQUIRED_SOUNDS)
    report["refilled_from_canonical"] += c
    report["locked_skips"] += l

    c, l = _refill_missing_from_canonical(message_dst, SRC_MESSAGE, ["message.html"])
    report["refilled_from_canonical"] += c
    report["locked_skips"] += l

    c, l = _refill_missing_from_canonical(message_dst, SRC_MESSAGE, ["message.png"])
    report["refilled_from_canonical"] += c
    report["locked_skips"] += l

    # Rewrite paths (safe on locks)
    r, lk = _rewrite_paths(build_dir / "index.html")
    report["rewrote_index"] = r
    report["rewrote_locked"] += int(lk)

    r, lk = _rewrite_paths(build_dir / "script.js")
    report["rewrote_script"] = r
    report["rewrote_locked"] += int(lk)

    r, lk = _rewrite_paths(build_dir / "styles.css")
    report["rewrote_css"] = r
    report["rewrote_locked"] += int(lk)

    # Post-check
    missing = []
    for name in REQUIRED_PAGES:
        if not (pages_dst / name).is_file():
            missing.append(f"pages/{name}")
    for name in REQUIRED_CONTROLS:
        if not (controls_dst / name).is_file():
            missing.append(f"controls/{name}")
    for name in REQUIRED_SOUNDS:
        if not (sounds_dst / name).is_file():
            missing.append(f"sounds/{name}")
    report["missing_after"] = missing

    return report


def main() -> None:
    if not PLAY_ROOT.is_dir():
        print(f"[ERR] Missing Play root: {PLAY_ROOT}")
        return

    builds = [p for p in PLAY_ROOT.iterdir() if p.is_dir()]
    if not builds:
        print("[OK] No Play builds found.")
        return

    print(f"[PATCH] Play root: {PLAY_ROOT}")
    print(f"[PATCH] Builds found: {len(builds)}")
    print()

    patched = 0
    total_locked = 0
    total_rewrite_locked = 0

    for b in sorted(builds, key=lambda p: p.name.lower()):
        r = patch_one_build(b)
        if not r["gallery_found"]:
            print(f"- {b.name}: SKIP (no gallery/)")
            continue

        patched += 1
        total_locked += r["locked_skips"]
        total_rewrite_locked += r["rewrote_locked"]

        miss = r["missing_after"]
        miss_str = "OK" if not miss else f"MISSING {len(miss)}"

        print(
            f"- {b.name}: "
            f"copy(build)={r['copied_from_build']} "
            f"refill(canon)={r['refilled_from_canonical']} "
            f"locked_skips={r['locked_skips']} "
            f"rewrite(i/s/c)={int(r['rewrote_index'])}/{int(r['rewrote_script'])}/{int(r['rewrote_css'])} "
            f"rewrite_locked={r['rewrote_locked']} "
            f"=> {miss_str}"
        )
        if miss:
            for m in miss:
                print(f"    - {m}")

    print()
    print(f"[DONE] Patched {patched}/{len(builds)} builds.")
    if total_locked or total_rewrite_locked:
        print(f"[NOTE] Locked-file skips: copy={total_locked}, rewrite={total_rewrite_locked}")
        print("       Close any open Play build tabs/windows (or Explorer preview) and rerun for a perfect patch.")


if __name__ == "__main__":
    main()
