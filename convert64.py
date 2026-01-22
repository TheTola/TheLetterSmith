#!/usr/bin/env python3
"""
convert64.py — Base64 packer for Letter Smith
------------------------------------------------
Scans a project tree for common image and audio assets, encodes them to
Base64 data-URIs, and emits a Python module at:
    <output_dir>/convert64.py
containing two dictionaries:
    BASE64_IMAGES : { "relative/posix/path": "data:<mime>;base64,..." }
    AUDIO_URIS    : { "relative/posix/path": "data:<mime>;base64,..." }

• Respects settings.json via config.Config (exclude_dirs, exclude_exts, output_dir, verbose)
• Atomic writes (safe on Windows)
• Deterministic ordering
• Optional filters (CLI): --images-only / --audio-only / --max-bytes / --dry-run
• Skips excluded directories and extensions
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

# Project config (reads settings.json at the chosen root)
from config import Config

# ──────────────────────────────────────────────────────────────────────────
# Supported media extensions → MIME types
# Extend consciously; keep defaults conservative for stability.
# ──────────────────────────────────────────────────────────────────────────
IMAGE_MIME: Dict[str, str] = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
}
AUDIO_MIME: Dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".aac": "audio/aac",
    # Note: m4a/flac omitted intentionally; add if/when needed.
}

# ──────────────────────────────────────────────────────────────────────────
# Datatypes
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Task:
    path: Path
    rel_key: str
    mime: str

# ──────────────────────────────────────────────────────────────────────────
# Discovery
# ──────────────────────────────────────────────────────────────────────────
def _should_skip(path: Path, exclude_dirs: Set[str], exclude_exts: Set[str]) -> bool:
    if not path.is_file():
        return True
    if any(part in exclude_dirs for part in path.parts):
        return True
    ext = path.suffix.lower()
    if ext in exclude_exts:
        return True
    return False

def _gather_tasks(root: Path,
                  include_exts: Set[str],
                  exclude_dirs: Set[str],
                  exclude_exts: Set[str],
                  mime_map: Dict[str, str]) -> List[Task]:
    tasks: List[Task] = []
    for path in root.rglob("*"):
        if _should_skip(path, exclude_dirs, exclude_exts):
            continue
        ext = path.suffix.lower()
        if ext in include_exts:
            rel = path.relative_to(root).as_posix()
            tasks.append(Task(path=path, rel_key=rel, mime=mime_map.get(ext, "application/octet-stream")))
    return tasks

# ──────────────────────────────────────────────────────────────────────────
# Encoding
# ──────────────────────────────────────────────────────────────────────────
def _encode_task(task: Task, max_bytes: Optional[int] = None) -> Tuple[str, Optional[str]]:
    """Return (rel_key, data_uri_or_none). None if filtered or read fails."""
    try:
        data = task.path.read_bytes()
    except Exception as e:
        logging.warning(f"⚠️ Failed to read {task.path}: {e}")
        return task.rel_key, None

    if max_bytes is not None and len(data) > max_bytes:
        logging.info(f"⏭️ Skipping (>{max_bytes} bytes): {task.rel_key}")
        return task.rel_key, None

    b64 = base64.b64encode(data).decode("ascii")
    return task.rel_key, f"data:{task.mime};base64,{b64}"

def build_maps(root: Path,
               img_tasks: List[Task],
               aud_tasks: List[Task],
               max_bytes: Optional[int] = None,
               workers: int = 0) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Encode with optional thread pool. Returns (images_map, audio_map)."""
    images: Dict[str, str] = {}
    audios: Dict[str, str] = {}

    all_tasks = [("img", t) for t in img_tasks] + [("aud", t) for t in aud_tasks]
    if not all_tasks:
        return images, audios

    if workers <= 0:
        workers = min(8, max(1, os.cpu_count() or 1))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut_to_kind = {ex.submit(_encode_task, t, max_bytes): kind for kind, t in all_tasks}
        for fut in as_completed(fut_to_kind):
            kind = fut_to_kind[fut]
            try:
                rel, uri = fut.result()
            except Exception as e:
                logging.warning(f"⚠️ Worker failed: {e}")
                continue
            if uri is None:
                continue
            if kind == "img":
                images[rel] = uri
            else:
                audios[rel] = uri

    return images, audios

# ──────────────────────────────────────────────────────────────────────────
# Emission
# ──────────────────────────────────────────────────────────────────────────
def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)

def _existing_payload(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None

def _render_module(images: Dict[str, str], audios: Dict[str, str]) -> str:
    # Deterministic order
    lines: List[str] = []
    lines.append("# Auto-generated base64 media dictionaries\n# flake8: noqa\n# fmt: off\n\n")
    lines.append("BASE64_IMAGES = {\n")
    for rel in sorted(images.keys()):
        lines.append(f"    {rel!r}: {images[rel]!r},\n")
    lines.append("}\n\n")
    lines.append("AUDIO_URIS = {\n")
    for rel in sorted(audios.keys()):
        lines.append(f"    {rel!r}: {audios[rel]!r},\n")
    lines.append("}\n")
    return "".join(lines)

def write_module(out_dir: Path, images: Dict[str, str], audios: Dict[str, str], dry_run: bool = False) -> Path:
    """Write <out_dir>/convert64.py (and __init__.py). Returns path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    init_file = out_dir / "__init__.py"
    if not init_file.exists() and not dry_run:
        init_file.write_text("# converted64 package\n", encoding="utf-8")
        logging.info(f"Created package init: {init_file}")

    target = out_dir / "convert64.py"
    payload = _render_module(images, audios)

    # Skip write if no change
    existing = _existing_payload(target)
    if existing == payload:
        logging.info(f"✅ Base64 bundle up-to-date at {target} ({len(images)} images, {len(audios)} audio)." )
        return target

    if dry_run:
        logging.info(f"[dry-run] Would write module to {target}")
        return target

    _atomic_write(target, payload)
    logging.info(f"✅ Wrote module with {len(images)} images & {len(audios)} audio URIs to {target}")
    return target

# ──────────────────────────────────────────────────────────────────────────
# CLI / Entrypoint
# ──────────────────────────────────────────────────────────────────────────
def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate <output_dir>/convert64.py with image and audio data-URIs")
    p.add_argument("project_root", nargs="?", default=".", help="Folder to scan (defaults to current directory)")
    p.add_argument("--images-only", action="store_true", help="Only encode images (skip audio)")
    p.add_argument("--audio-only", action="store_true", help="Only encode audio (skip images)")
    p.add_argument("--max-bytes", type=int, default=None, help="Skip files larger than this many bytes (optional)")
    p.add_argument("-j", "--jobs", type=int, default=0, help="Worker threads (0 = auto)")
    p.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    p.add_argument("--dry-run", action="store_true", help="Scan + report but don’t write files")
    return p.parse_args(argv)

def main(project_root: Optional[str] = None) -> None:
    """Main entry point — accepts optional project_root for GUI integration."""
    args = _parse_args(None)
    if project_root:
        args.project_root = project_root

    root = Path(args.project_root).resolve()
    cfg = Config(root)

    # Logging
    level = logging.DEBUG if args.verbose or getattr(cfg, "verbose", False) else logging.INFO
    logging.basicConfig(level=level, format="%(message)s")

    # Settings
    exclude_dirs = set(getattr(cfg, "exclude_dirs", []))
    exclude_exts = set(getattr(cfg, "exclude_exts", []))
    out_dir = Path(getattr(cfg, "output_dir", "converted64"))
    if not out_dir.is_absolute():
        out_dir = root / out_dir

    # Gather
    img_exts = set(IMAGE_MIME.keys())
    aud_exts = set(AUDIO_MIME.keys())

    img_tasks: List[Task] = []
    aud_tasks: List[Task] = []

    if not args.audio_only:
        img_tasks = _gather_tasks(root, img_exts, exclude_dirs, exclude_exts, IMAGE_MIME)
    if not args.images_only:
        # For audio, respect exclude_dirs but not exclude_exts (we already filter by include list)
        aud_tasks = _gather_tasks(root, aud_exts, exclude_dirs, set(), AUDIO_MIME)

    logging.info(f"Found {len(img_tasks)} image(s) and {len(aud_tasks)} audio file(s) to encode.")

    # Encode
    images, audios = build_maps(root, img_tasks, aud_tasks, max_bytes=args.max_bytes, workers=args.jobs)

    # Write
    write_module(out_dir, images, audios, dry_run=args.dry_run)