#!/usr/bin/env python3
# File: target.py
# -*- coding: utf-8 -*-

"""
TARGET — Folder Structure Extractor (Save-first, Futuristic UI, Standalone)

What this tool is for
- Generate a clean, paste-friendly representation of a folder’s structure so you can:
  1) comprehend a project layout quickly, and/or
  2) save/share it (including pasting into ChatGPT later).

Key behaviors
- SAVE-FIRST. Results popup buttons: Save → Open → Cancel (no Save As).
- Cancel is red. Popup is wider. Buttons never overflow.
- Open works even if you never saved: opens Notepad++ (or fallback) via temp file.
- App icon is set using project icon fallbacks.
- Prevents “modal dialog behind window” freezes.

Standard library only.
"""

from __future__ import annotations

import argparse
import fnmatch
import math
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox

from app_icon import canonical_icon_paths, configure_windows_app_identity

# ─────────────────────────────────────────────────────────────────────────────
# Small color helpers (UI accents)
# ─────────────────────────────────────────────────────────────────────────────

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _lerp(a: int, b: int, t: float) -> int:
    t = _clamp01(t)
    return int(round(a + (b - a) * t))


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _blend(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex(_lerp(r1, r2, t), _lerp(g1, g2, t), _lerp(b1, b2, t))


def _now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _safe_basename(p: str) -> str:
    b = os.path.basename(os.path.normpath(p)) or "ROOT"
    return "".join(ch for ch in b if ch.isalnum() or ch in ("-", "_", "."))[:64] or "ROOT"


# ─────────────────────────────────────────────────────────────────────────────
# Defaults (professional “common junk” ignores)
# ─────────────────────────────────────────────────────────────────────────────

COMMON_EXCLUDE_DIR_NAMES = {
    ".git", ".svn", ".hg",
    "__pycache__", ".mypy_cache", ".pytest_cache",
    "node_modules",
    ".venv", "venv",
    "dist", "build",
    ".idea", ".vs",
    ".cache",
}

COMMON_EXCLUDE_GLOBS = [
    "*.pyc", "*.pyo", "*.pyd",
    "*.obj", "*.log", "*.tmp", "*.temp",
    "*.DS_Store", "Thumbs.db",
]


# ─────────────────────────────────────────────────────────────────────────────
# Scan model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScanOptions:
    include_files: bool = True
    include_dirs: bool = True
    absolute_paths: bool = False
    max_depth: int = 0
    max_items: int = 50_000
    follow_symlinks: bool = False
    use_common_excludes: bool = True
    exclude_dir_names: set[str] = field(default_factory=set)
    exclude_globs: list[str] = field(default_factory=list)
    format_mode: str = "tree"  # "tree" | "flat" | "markdown"
    include_header: bool = True
    sort: bool = True


@dataclass
class ScanStats:
    scanned_dirs: int = 0
    scanned_files: int = 0
    collected: int = 0
    errors: int = 0
    truncated: bool = False
    elapsed_sec: float = 0.0


@dataclass
class Entry:
    rel_path: str
    is_dir: bool


@dataclass
class ScanResult:
    root: str
    entries: list[Entry]
    stats: ScanStats


ProgressCallback = Callable[[ScanStats], None]


# ─────────────────────────────────────────────────────────────────────────────
# Scanning engine (fast + safe)
# ─────────────────────────────────────────────────────────────────────────────

def _matches_any_glob(name: str, globs: Iterable[str]) -> bool:
    for pat in globs:
        if pat and fnmatch.fnmatch(name, pat):
            return True
    return False


def scan_folder(
    root_path: str,
    options: ScanOptions,
    progress: Optional[ProgressCallback] = None,
    cancel_flag: Optional[threading.Event] = None,
) -> ScanResult:
    start = time.perf_counter()
    root_path = os.path.abspath(root_path)

    ex_dir_names = set(options.exclude_dir_names)
    ex_globs = list(options.exclude_globs)

    if options.use_common_excludes:
        ex_dir_names |= COMMON_EXCLUDE_DIR_NAMES
        ex_globs = COMMON_EXCLUDE_GLOBS + ex_globs

    stack: list[tuple[str, str, int]] = [(root_path, "", 0)]
    out: list[Entry] = []
    stats = ScanStats()

    last_ui_ping = 0.0
    UI_PING_INTERVAL = 0.06

    def ping_ui(force: bool = False) -> None:
        nonlocal last_ui_ping
        if not progress:
            return
        now = time.perf_counter()
        if force or (now - last_ui_ping) >= UI_PING_INTERVAL:
            last_ui_ping = now
            progress(stats)

    max_depth = max(0, int(options.max_depth))
    depth_limited = (max_depth > 0)
    max_items = max(1, int(options.max_items))

    while stack:
        if cancel_flag and cancel_flag.is_set():
            break

        abs_dir, rel_dir, depth = stack.pop()

        if depth_limited and depth > max_depth:
            continue

        try:
            with os.scandir(abs_dir) as it:
                stats.scanned_dirs += 1
                children: list[os.DirEntry] = list(it)
        except Exception:
            stats.errors += 1
            ping_ui()
            continue

        if options.sort:
            children.sort(key=lambda d: d.name.lower())

        for de in children:
            if cancel_flag and cancel_flag.is_set():
                break

            name = de.name

            if _matches_any_glob(name, ex_globs):
                continue

            try:
                is_dir = de.is_dir(follow_symlinks=options.follow_symlinks)
            except Exception:
                stats.errors += 1
                continue

            if is_dir and (name in ex_dir_names):
                continue

            rel = os.path.join(rel_dir, name) if rel_dir else name

            if is_dir:
                if options.include_dirs:
                    out.append(Entry(rel_path=rel, is_dir=True))
                    stats.collected += 1

                if not depth_limited or (depth + 1) <= max_depth:
                    try:
                        stack.append((de.path, rel, depth + 1))
                    except Exception:
                        stats.errors += 1
            else:
                stats.scanned_files += 1
                if options.include_files:
                    out.append(Entry(rel_path=rel, is_dir=False))
                    stats.collected += 1

            if stats.collected >= max_items:
                stats.truncated = True
                stack.clear()
                break

            ping_ui()

    stats.elapsed_sec = max(0.0, time.perf_counter() - start)
    ping_ui(force=True)

    return ScanResult(root=root_path, entries=out, stats=stats)


# ─────────────────────────────────────────────────────────────────────────────
# Output formatting
# ─────────────────────────────────────────────────────────────────────────────

def _path_for_output(root: str, rel: str, absolute: bool) -> str:
    if absolute:
        return os.path.abspath(os.path.join(root, rel))
    return rel.replace("\\", "/")


def format_output(result: ScanResult, options: ScanOptions) -> str:
    mode = (options.format_mode or "tree").strip().lower()
    entries = result.entries

    if options.sort:
        def k(e: Entry):
            parts = tuple(e.rel_path.replace("\\", "/").split("/"))
            return (parts, 0 if e.is_dir else 1)
        entries = sorted(entries, key=k)

    lines: list[str] = []

    if options.include_header:
        lines.append(f"ROOT: {result.root}")
        lines.append(f"MODE: {mode.upper()} | PATHS: {'ABS' if options.absolute_paths else 'REL'}")
        lines.append(
            f"ITEMS: {result.stats.collected:,} | "
            f"FILES_SCANNED: {result.stats.scanned_files:,} | "
            f"DIRS_SCANNED: {result.stats.scanned_dirs:,} | "
            f"ERRORS: {result.stats.errors:,} | "
            f"TRUNCATED: {'YES' if result.stats.truncated else 'NO'} | "
            f"TIME: {result.stats.elapsed_sec:.2f}s"
        )
        lines.append("")

    if mode == "flat":
        for e in entries:
            lines.append(_path_for_output(result.root, e.rel_path, options.absolute_paths))
    elif mode == "markdown":
        lines.append("```")
        lines.extend(_format_tree_lines(result, options, entries))
        lines.append("```")
    else:
        lines.extend(_format_tree_lines(result, options, entries))

    return "\n".join(lines).rstrip() + "\n"


def _format_tree_lines(result: ScanResult, options: ScanOptions, entries: list[Entry]) -> list[str]:
    root_label = _safe_basename(result.root) + "/"
    out: list[str] = [root_label]
    printed_dirs: set[str] = set()

    def ensure_dir(rel_dir: str) -> None:
        if not rel_dir:
            return
        parts = rel_dir.replace("\\", "/").split("/")
        accum = []
        for i, p in enumerate(parts):
            accum.append(p)
            key = "/".join(accum)
            if key in printed_dirs:
                continue
            indent = "  " * (i + 1)
            out.append(f"{indent}{p}/")
            printed_dirs.add(key)

    for e in entries:
        rel = e.rel_path.replace("\\", "/")
        parent = os.path.dirname(rel).replace("\\", "/")
        parent = "" if parent in (".", "") else parent

        ensure_dir(parent)

        depth = 1 + (0 if not parent else parent.count("/") + 1)
        name = os.path.basename(rel)

        if e.is_dir:
            ensure_dir(rel)
        else:
            indent = "  " * depth
            out.append(f"{indent}{name}")

    if options.absolute_paths:
        abs_out: list[str] = [result.root]
        for line in out[1:]:
            stripped = line.lstrip(" ")
            indent_spaces = len(line) - len(stripped)
            relish = stripped.rstrip("/")
            if not relish:
                abs_out.append(line)
                continue
            abs_path = os.path.abspath(os.path.join(result.root, relish))
            suffix = "/" if stripped.endswith("/") else ""
            abs_out.append((" " * indent_spaces) + abs_path + suffix)
        return abs_out

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Saving + editor helpers
# ─────────────────────────────────────────────────────────────────────────────

def default_export_dir() -> str:
    home = str(Path.home())
    desktop = os.path.join(home, "Desktop")
    downloads = os.path.join(home, "Downloads")
    for cand in (desktop, downloads, home, os.getcwd()):
        if os.path.isdir(cand):
            return cand
    return os.getcwd()


def default_export_path(root_folder: str) -> str:
    base = _safe_basename(root_folder)
    return os.path.join(default_export_dir(), f"TARGET_{base}_{_now_stamp()}.txt")


def write_text_file(path: str, text: str) -> None:
    Path(os.path.dirname(path) or ".").mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _find_notepadpp() -> Optional[str]:
    if os.name != "nt":
        return None
    p = shutil.which("notepad++.exe")
    if p:
        return p
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates = [
        os.path.join(pf, "Notepad++", "notepad++.exe"),
        os.path.join(pf86, "Notepad++", "notepad++.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def open_text_in_editor(text: str, *, suggested_name: str = "TARGET_preview.txt") -> Optional[str]:
    try:
        tmp_dir = Path(os.environ.get("TEMP") or os.environ.get("TMP") or str(Path.home()))
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"{Path(suggested_name).stem}_{_now_stamp()}.txt"
        write_text_file(str(tmp_path), text)
    except Exception:
        return None

    try:
        npp = _find_notepadpp()
        if npp:
            subprocess.Popen([npp, str(tmp_path)], close_fds=True)
            return str(tmp_path)

        if os.name == "nt":
            subprocess.Popen(["notepad.exe", str(tmp_path)], close_fds=True)
            return str(tmp_path)

        if sys.platform == "darwin":
            subprocess.Popen(["open", str(tmp_path)])
            return str(tmp_path)

        subprocess.Popen(["xdg-open", str(tmp_path)])
        return str(tmp_path)
    except Exception:
        return None


def open_in_file_explorer_select(path: str) -> None:
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
            return
        folder = os.path.dirname(path)
        subprocess.Popen(["xdg-open", folder])
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Icon helpers
# ─────────────────────────────────────────────────────────────────────────────

def find_app_icon(base_dir: Path) -> Optional[Path]:
    _, canonical_ico = canonical_icon_paths(base_dir)
    candidates = (
        canonical_ico,
        base_dir / "gallery" / "icon" / "ls-icon.ico",
        base_dir / "gallery" / "icon" / "LSmith.ico",
        base_dir / "gallery" / "icons" / "LSmith.ico",
        base_dir / "gallery" / "icons" / "ls-icon.ico",
    )
    for p in candidates:
        if p.exists():
            return p
    return None


def apply_window_icon(win: tk.Tk | tk.Toplevel, icon_path: Optional[Path]) -> None:
    if not icon_path:
        return

    icon_path = Path(icon_path)
    png_path = icon_path if icon_path.suffix.lower() == ".png" else icon_path.with_suffix(".png")
    ico_path = icon_path if icon_path.suffix.lower() == ".ico" else icon_path.with_suffix(".ico")

    if png_path.is_file():
        try:
            photo = tk.PhotoImage(file=str(png_path))
            win.iconphoto(True, photo)
            setattr(win, "_letter_smith_icon_photo", photo)
        except Exception:
            pass

    try:
        if os.name == "nt" and ico_path.is_file():
            win.iconbitmap(str(ico_path))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Spinner overlay
# ─────────────────────────────────────────────────────────────────────────────

class SpinnerOverlay(tk.Toplevel):
    def __init__(self, parent: tk.Tk, icon_path: Optional[Path]):
        super().__init__(parent)

        self.withdraw()
        self.overrideredirect(True)
        self.configure(bg="#0f121a")
        self.attributes("-alpha", 0.0)
        self.transient(parent)
        self.lift(parent)
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass

        apply_window_icon(self, icon_path)

        self.canvas = tk.Canvas(self, width=210, height=132, bg="#0f121a", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self._angle = 0.0
        self._running = True
        self._fade_in_step = 0
        self._status = "Scanning…"
        self._count = 0

        self._center_over_parent(parent, 210, 132)
        self.update_idletasks()
        self.deiconify()

        self.after(10, self._fade_in)
        self.after(0, self._tick)

    def _center_over_parent(self, parent: tk.Tk, w: int, h: int) -> None:
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        x, y = px + (pw - w) // 2, py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def set_status(self, status: str, count: int) -> None:
        self._status = status
        self._count = count

    def _fade_in(self) -> None:
        if self._fade_in_step <= 10:
            self.attributes("-alpha", self._fade_in_step / 10.0 * 0.96 + 0.04)
            self._fade_in_step += 1
            self.after(16, self._fade_in)

    def _tick(self) -> None:
        if not self._running:
            return
        self._angle = (self._angle + 10) % 360
        self._draw()
        self.after(60, self._tick)

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        w = int(c["width"])
        h = int(c["height"])
        cx, cy = w // 2, h // 2

        c.create_oval(cx - 56, cy - 56, cx + 56, cy + 56, outline="#1c2340", width=3)

        spokes = 12
        radius = 42
        for i in range(spokes):
            a = math.radians(self._angle + (360 / spokes) * i)
            x1 = cx + math.cos(a) * (radius - 10)
            y1 = cy + math.sin(a) * (radius - 10)
            x2 = cx + math.cos(a) * radius
            y2 = cy + math.sin(a) * radius
            alpha = (i + (self._angle / 360) * spokes) % spokes / (spokes - 1)
            color = _blend("#3357ff", "#66b5ff", alpha)
            c.create_line(x1, y1, x2, y2, fill=color, width=3, capstyle="round")

        c.create_text(cx, cy + 40, text=self._status, fill="#e7eaf2", font=("Segoe UI", 10))
        c.create_text(cx, cy + 58, text=f"{self._count:,} item(s)…", fill="#aab2c8", font=("Segoe UI", 9))

    def close(self) -> None:
        self._running = False
        try:
            self.destroy()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Tiny post-scan prompt
# ─────────────────────────────────────────────────────────────────────────────

class TinyResultPrompt(tk.Toplevel):
    def __init__(self, parent: "TargetApp", output_text: str, result: ScanResult, scan_opts: ScanOptions):
        super().__init__(parent)

        self.withdraw()

        self._app = parent
        self._output_text = output_text
        self._result = result
        self._scan_opts = scan_opts

        self.overrideredirect(True)
        self.configure(bg="#0f121a")
        self.transient(parent)

        try:
            self.attributes("-topmost", True)
        except Exception:
            pass

        self.lift(parent)
        apply_window_icon(self, parent.icon_path)

        w, h = 400, 156

        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        x, y = px + (pw - w) // 2, py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        body = tk.Canvas(self, width=w, height=h, bg="#0f121a", highlightthickness=0, bd=0)
        body.pack(fill="both", expand=True)

        self._roundrect(body, 4, 4, w - 4, h - 4, r=14, fill="#0f121a", outline="#2a3150")

        truncated = "YES" if result.stats.truncated else "NO"
        body.create_text(
            w // 2, 34,
            text=f"Found {result.stats.collected:,} item(s)\nErrors: {result.stats.errors:,} | Truncated: {truncated}",
            fill="#e7e8ea", font=("Segoe UI", 10), justify="center"
        )
        body.create_text(
            w // 2, 74,
            text=f"Format: {scan_opts.format_mode.upper()} | Output: .txt",
            fill="#aab2c8", font=("Segoe UI", 9), justify="center"
        )

        margin = 14
        gap = 10
        bw = (w - 2 * margin - 2 * gap) // 3
        bh = 28
        yb = 116

        def mk_btn(ix: int, text: str, bg: str, bgh: str, fg: str, cmd) -> None:
            x0 = margin + ix * (bw + gap)
            rect = body.create_rectangle(x0, yb, x0 + bw, yb + bh, fill=bg, outline="#2c3458", width=1)
            label = body.create_text(x0 + bw / 2, yb + bh / 2, text=text, fill=fg, font=("Segoe UI", 9))

            def enter(_): body.itemconfig(rect, fill=bgh)
            def leave(_): body.itemconfig(rect, fill=bg)
            def click(_): cmd()

            for tag in (rect, label):
                body.tag_bind(tag, "<Enter>", enter)
                body.tag_bind(tag, "<Leave>", leave)
                body.tag_bind(tag, "<Button-1>", click)

        def do_save() -> None:
            path = self._app.quick_save(self._output_text)
            if path:
                self._app._last_saved_path = path
                self._app._set_action_buttons_enabled(True)
                self._confetti_burst()  # FIXED: valid background color
                self.after(220, self.destroy)

        def do_open() -> None:
            self._app.open_current_in_editor()

        def do_cancel() -> None:
            self.destroy()

        mk_btn(0, "Save",   "#2a9d8f", "#37b3a5", "#ffffff", do_save)
        mk_btn(1, "Open",   "#8b5cf6", "#a78bfa", "#ffffff", do_open)
        mk_btn(2, "Cancel", "#c24141", "#e05555", "#ffffff", do_cancel)

        self._drag = None

        def start_move(e):
            self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

        def on_move(e):
            if self._drag:
                dx, dy = self._drag
                self.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

        body.bind("<Button-1>", start_move)
        body.bind("<B1-Motion>", on_move)
        self.bind("<Escape>", lambda _e: self.destroy())

        self.update_idletasks()
        self.deiconify()
        try:
            self.grab_set()
        except Exception:
            pass
        try:
            self.focus_force()
        except Exception:
            pass

    @staticmethod
    def _roundrect(c: tk.Canvas, x1: int, y1: int, x2: int, y2: int, r: int = 12, **kw) -> int:
        points = [
            x1 + r, y1, x2 - r, y1,
            x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r,
            x1, y1 + r, x1, y1,
        ]
        return c.create_polygon(points, **kw, smooth=True)

    def _confetti_burst(self) -> None:
        w = self.winfo_width()
        h = self.winfo_height()

        # FIX: bg="" is invalid on Windows Tk. Use a real color or omit bg.
        c = tk.Canvas(self, width=w, height=h, bg="#0f121a", highlightthickness=0, bd=0)
        c.place(x=0, y=0)

        parts = []
        colors = ["#66b5ff", "#99d1ff", "#6ae6bf", "#ffd166", "#ff6aa6", "#a78bfa"]
        for _ in range(22):
            x = random.randint(30, w - 30)
            y = random.randint(20, h - 30)
            r = random.randint(2, 4)
            vx = random.uniform(-1.4, 1.4)
            vy = random.uniform(-2.6, -0.7)
            parts.append([c.create_oval(x - r, y - r, x + r, y + r, fill=random.choice(colors), width=0), x, y, vx, vy, 0])

        def tick():
            done = True
            for p in parts:
                item, x, y, vx, vy, t = p
                t += 1
                x += vx
                y += vy
                vy += 0.10
                p[1], p[2], p[4], p[5] = x, y, vy, t
                c.move(item, vx, vy)
                if 0 <= x <= w and 0 <= y <= h and t < 24:
                    done = False
            if not done:
                self.after(16, tick)
            else:
                c.destroy()

        tick()


# ─────────────────────────────────────────────────────────────────────────────
# Futuristic app UI
# ─────────────────────────────────────────────────────────────────────────────

class TargetApp(tk.Tk):
    def __init__(self, *, preset: Optional[str] = None, icon_path: Optional[Path] = None):
        super().__init__()

        self.icon_path = icon_path
        apply_window_icon(self, self.icon_path)

        self.overrideredirect(True)
        self.geometry("1000x450")
        self.minsize(560, 360)
        self.configure(bg="#0b0d12")

        self._drag = None
        self._shimmer_t = 0.0
        self._pulse_t = 0.0

        self._current_folder: Optional[str] = None
        self._current_result: Optional[ScanResult] = None
        self._current_output: str = ""
        self._last_saved_path: Optional[str] = None
        self._last_temp_open_path: Optional[str] = None

        self.var_format = tk.StringVar(value="tree")
        self.var_paths = tk.StringVar(value="Relative")
        self.var_depth = tk.IntVar(value=0)
        self.var_max_items = tk.IntVar(value=50_000)
        self.var_common_excludes = tk.BooleanVar(value=True)
        self.var_include_files = tk.BooleanVar(value=True)
        self.var_include_dirs = tk.BooleanVar(value=True)
        self.var_header = tk.BooleanVar(value=True)
        self.var_excludes = tk.StringVar(value="")
        self.var_sort = tk.BooleanVar(value=True)

        if preset:
            self.apply_preset(preset)

        self._build_titlebar()
        self._build_body()
        self.after(16, self._animate)

    # ---- Titlebar ----

    def _build_titlebar(self) -> None:
        h = 36
        self.titlebar = tk.Canvas(self, height=h, bg="#141724", highlightthickness=0, bd=0)
        self.titlebar.pack(fill="x", side="top")

        self._title_shimmer_id = self.titlebar.create_rectangle(0, 0, 0, h, fill="#1a1e34", width=0)
        self.titlebar.bind("<Button-1>", self._start_move)
        self.titlebar.bind("<B1-Motion>", self._on_move)

        self.titlebar.create_text(14, h // 2, text="TARGET", anchor="w", fill="#00ffff",
                                  font=("Segoe UI Semibold", 12))

        self._btn_min_rect = self.titlebar.create_rectangle(0, 0, 0, 0, fill="#141724", outline="")
        self._btn_min_text = self.titlebar.create_text(0, 0, text="–", fill="#e6e6e6", font=("Segoe UI", 12))
        self._btn_close_rect = self.titlebar.create_rectangle(0, 0, 0, 0, fill="#141724", outline="")
        self._btn_close_text = self.titlebar.create_text(0, 0, text="×", fill="#e6e6e6", font=("Segoe UI Semibold", 12))

        self.titlebar.bind("<Configure>", self._layout_titlebar)

        for tag in (self._btn_min_rect, self._btn_min_text):
            self.titlebar.tag_bind(tag, "<Button-1>", lambda _e: self.iconify())

        for tag in (self._btn_close_rect, self._btn_close_text):
            self.titlebar.tag_bind(tag, "<Button-1>", lambda _e: self.destroy())

    def _layout_titlebar(self, _evt=None) -> None:
        w = self.titlebar.winfo_width()
        h = self.titlebar.winfo_height()

        self.titlebar.coords(self._title_shimmer_id, -100, 0, 0, h)

        bw = 40
        bx = w
        self.titlebar.coords(self._btn_close_rect, bx - bw, 0, bx, h)
        self.titlebar.coords(self._btn_close_text, bx - bw / 2, h / 2)
        bx -= bw
        self.titlebar.coords(self._btn_min_rect, bx - bw, 0, bx, h)
        self.titlebar.coords(self._btn_min_text, bx - bw / 2, h / 2)

    # ---- Body ----

    def _build_body(self) -> None:
        self.top = tk.Frame(self, bg="#0b0d12")
        self.top.pack(fill="x", padx=14, pady=(10, 6))

        self.btn_canvas = tk.Canvas(self.top, height=64, bg="#0b0d12", highlightthickness=0, bd=0)
        self.btn_canvas.pack(fill="x")
        self.btn_canvas.bind("<Configure>", self._layout_select_button)

        self.settings = tk.Frame(self.top, bg="#0b0d12")
        self.settings.pack(fill="x", pady=(8, 0))

        def lab(text: str) -> tk.Label:
            return tk.Label(self.settings, text=text, bg="#0b0d12", fg="#aab2c8", font=("Segoe UI", 9))

        def opt(parent, var: tk.StringVar, values: list[str], width: int) -> tk.OptionMenu:
            om = tk.OptionMenu(parent, var, *values)
            om.configure(
                bg="#141724", fg="#e7eaf2", activebackground="#1a1f38", activeforeground="#ffffff",
                highlightthickness=0, bd=0, relief="flat", font=("Segoe UI", 9), width=width
            )
            om["menu"].configure(bg="#141724", fg="#e7eaf2", activebackground="#2a4fe2", activeforeground="#ffffff")
            return om

        def spin(parent, var: tk.IntVar, from_: int, to: int, width: int) -> tk.Spinbox:
            return tk.Spinbox(
                parent, from_=from_, to=to, textvariable=var, width=width,
                bg="#141724", fg="#e7eaf2", insertbackground="#e7eaf2",
                highlightthickness=0, bd=0, relief="flat", font=("Segoe UI", 9)
            )

        def chk(parent, text: str, var: tk.BooleanVar) -> tk.Checkbutton:
            return tk.Checkbutton(
                parent, text=text, variable=var, bg="#0b0d12", fg="#e7eaf2",
                selectcolor="#141724", activebackground="#0b0d12",
                activeforeground="#ffffff", font=("Segoe UI", 9)
            )

        lab("Format").grid(row=0, column=0, sticky="w", padx=(0, 6))
        opt(self.settings, self.var_format, ["tree", "flat", "markdown"], width=9).grid(row=0, column=1, sticky="w", padx=(0, 14))

        lab("Paths").grid(row=0, column=2, sticky="w", padx=(0, 6))
        opt(self.settings, self.var_paths, ["Relative", "Absolute"], width=6).grid(row=0, column=3, sticky="w", padx=(0, 14))

        lab("Depth (0=∞)").grid(row=0, column=4, sticky="w", padx=(0, 6))
        spin(self.settings, self.var_depth, 0, 99, width=4).grid(row=0, column=5, sticky="w", padx=(0, 14))

        lab("Max items").grid(row=0, column=6, sticky="w", padx=(0, 6))
        spin(self.settings, self.var_max_items, 1000, 2_000_000, width=8).grid(row=0, column=7, sticky="w", padx=(0, 14))

        chk(self.settings, "Common excludes", self.var_common_excludes).grid(row=0, column=8, sticky="w", padx=(0, 10))
        chk(self.settings, "Files", self.var_include_files).grid(row=0, column=9, sticky="w", padx=(0, 6))
        chk(self.settings, "Folders", self.var_include_dirs).grid(row=0, column=10, sticky="w", padx=(0, 10))
        chk(self.settings, "Header", self.var_header).grid(row=0, column=11, sticky="w", padx=(0, 10))
        chk(self.settings, "Sort", self.var_sort).grid(row=0, column=12, sticky="w")

        exrow = tk.Frame(self.top, bg="#0b0d12")
        exrow.pack(fill="x", pady=(8, 0))

        tk.Label(exrow, text="Custom excludes: ", bg="#0b0d12", fg="#aab2c8", font=("Segoe UI", 9)).pack(anchor="w")

        self.entry_ex = tk.Entry(
            exrow, textvariable=self.var_excludes,
            bg="#141724", fg="#e7eaf2", insertbackground="#e7eaf2",
            relief="flat", bd=0, highlightthickness=0, font=("Segoe UI", 9)
        )
        self.entry_ex.pack(fill="x", pady=(4, 0))

        self.mid = tk.Frame(self, bg="#0b0d12")
        self.mid.pack(fill="both", expand=True, padx=14, pady=(8, 10))

        tk.Label(self.mid, text="Preview:", bg="#0b0d12", fg="#aab2c8", font=("Segoe UI", 9)).pack(anchor="w")

        self.preview_frame = tk.Frame(self.mid, bg="#0b0d12")
        self.preview_frame.pack(fill="both", expand=True, pady=(6, 0))

        self.preview_text = tk.Text(
            self.preview_frame,
            bg="#0f121a",
            fg="#e7eaf2",
            insertbackground="#e7eaf2",
            font=("Cascadia Mono", 9),
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#2a3150",
            wrap="none",
        )
        self.preview_text.pack(side="left", fill="both", expand=True)
        self._bind_invisible_scroll(self.preview_text)

        self.bottom = tk.Frame(self, bg="#0b0d12")
        self.bottom.pack(fill="x", padx=14, pady=(0, 12))

        self.btn_save = tk.Button(self.bottom, text="Save", command=self.quick_save_current,
                                  bg="#2a9d8f", fg="#ffffff", activebackground="#37b3a5",
                                  relief="flat", bd=0, font=("Segoe UI Semibold", 10), padx=14, pady=6)
        self.btn_open = tk.Button(self.bottom, text="Open", command=self.open_current_in_editor,
                                  bg="#8b5cf6", fg="#ffffff", activebackground="#a78bfa",
                                  relief="flat", bd=0, font=("Segoe UI Semibold", 10), padx=14, pady=6)
        self.btn_clear = tk.Button(self.bottom, text="Clear", command=self.clear_output,
                                   bg="#232838", fg="#e7eaf2", activebackground="#2b3150",
                                   relief="flat", bd=0, font=("Segoe UI Semibold", 10), padx=14, pady=6)

        self.btn_save.pack(side="left", padx=(0, 10))
        self.btn_open.pack(side="left", padx=(0, 10))
        self.btn_clear.pack(side="right")

        self._set_action_buttons_enabled(False)

    def apply_preset(self, preset: str) -> None:
        p = (preset or "").strip().lower()
        if p == "chatgpt":
            self.var_format.set("tree")
            self.var_paths.set("Relative")
            self.var_depth.set(0)
            self.var_max_items.set(60_000)
            self.var_common_excludes.set(True)
            self.var_include_files.set(True)
            self.var_include_dirs.set(True)
            self.var_header.set(True)
            self.var_sort.set(True)
            self.var_excludes.set("dir:MAX,dir:Backups,dir:output,dir:converted64,*.mp3,*.wav,*.ogg")
        elif p == "flatabs":
            self.var_format.set("flat")
            self.var_paths.set("Absolute")
            self.var_header.set(False)

    def build_options_from_ui(self) -> ScanOptions:
        opts = ScanOptions()
        opts.format_mode = (self.var_format.get().strip().lower() or "tree")
        opts.absolute_paths = (self.var_paths.get().strip().lower() == "absolute")
        opts.max_depth = int(self.var_depth.get())
        opts.max_items = int(self.var_max_items.get())
        opts.use_common_excludes = bool(self.var_common_excludes.get())
        opts.include_files = bool(self.var_include_files.get())
        opts.include_dirs = bool(self.var_include_dirs.get())
        opts.include_header = bool(self.var_header.get())
        opts.sort = bool(self.var_sort.get())

        raw = self.var_excludes.get().strip()
        if raw:
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            for p in parts:
                if p.lower().startswith("dir:"):
                    name = p.split(":", 1)[1].strip()
                    if name:
                        opts.exclude_dir_names.add(name)
                else:
                    opts.exclude_globs.append(p)
        return opts

    def _run_modal_dialog(self, func, **kwargs):
        restore_topmost = None
        try:
            restore_topmost = bool(self.attributes("-topmost"))
        except Exception:
            restore_topmost = None

        try:
            try:
                self.attributes("-topmost", True)
            except Exception:
                pass
            self.update_idletasks()
            return func(parent=self, **kwargs)
        finally:
            if restore_topmost is not None:
                try:
                    self.attributes("-topmost", restore_topmost)
                except Exception:
                    pass

    def select_folder_flow(self) -> None:
        folder = self._run_modal_dialog(filedialog.askdirectory, title="Select Folder")
        if not folder:
            return
        self._current_folder = folder
        self.run_scan(folder)

    def run_scan(self, folder: str) -> None:
        opts = self.build_options_from_ui()
        self._set_action_buttons_enabled(False)
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("end", f"Scanning:\n{folder}\n\n")

        overlay = SpinnerOverlay(self, self.icon_path)
        cancel = threading.Event()

        def on_progress(stats: ScanStats) -> None:
            overlay.set_status("Scanning…", stats.collected)

        def worker() -> None:
            try:
                result = scan_folder(folder, opts, progress=lambda s: self.after(0, on_progress, s), cancel_flag=cancel)
                output = format_output(result, opts)
                self.after(0, self._after_scan, overlay, result, output, opts)
            except Exception as e:
                self.after(0, lambda: self._scan_failed(overlay, e))

        threading.Thread(target=worker, daemon=True).start()

    def _scan_failed(self, overlay: SpinnerOverlay, exc: Exception) -> None:
        overlay.close()
        messagebox.showerror("Scan failed", f"{type(exc).__name__}: {exc}")

    def _after_scan(self, overlay: SpinnerOverlay, result: ScanResult, output: str, opts: ScanOptions) -> None:
        overlay.close()
        self._current_result = result
        self._current_output = output
        self._last_saved_path = None
        self._last_temp_open_path = None

        lines = output.splitlines()
        preview_lines = lines[:400]
        if len(lines) > 400:
            preview_lines.append("")
            preview_lines.append(f"… preview truncated ({len(lines):,} total lines).")

        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("end", "\n".join(preview_lines) + "\n")
        self.preview_text.see("1.0")

        self._set_action_buttons_enabled(True)
        TinyResultPrompt(self, output, result, opts)

    def _set_action_buttons_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.btn_save.configure(state=state)
        self.btn_open.configure(state=state)

    def clear_output(self) -> None:
        self._current_result = None
        self._current_output = ""
        self._last_saved_path = None
        self._last_temp_open_path = None
        self.preview_text.delete("1.0", "end")
        self._set_action_buttons_enabled(False)

    def quick_save(self, text: str) -> Optional[str]:
        if not self._current_folder:
            return None
        path = default_export_path(self._current_folder)
        try:
            write_text_file(path, text)
            return path
        except Exception as e:
            messagebox.showerror("Save failed", f"{type(e).__name__}: {e}")
            return None

    def quick_save_current(self) -> None:
        if not self._current_output:
            return
        path = self.quick_save(self._current_output)
        if path:
            self._last_saved_path = path

    def open_current_in_editor(self) -> None:
        if not self._current_output:
            return

        if self._last_saved_path and os.path.isfile(self._last_saved_path):
            try:
                npp = _find_notepadpp()
                if npp:
                    subprocess.Popen([npp, self._last_saved_path], close_fds=True)
                else:
                    open_in_file_explorer_select(self._last_saved_path)
            except Exception:
                open_in_file_explorer_select(self._last_saved_path)
            return

        suggested = f"TARGET_{_safe_basename(self._current_folder or 'ROOT')}.txt"
        tmp_path = open_text_in_editor(self._current_output, suggested_name=suggested)
        if tmp_path:
            self._last_temp_open_path = tmp_path
        else:
            messagebox.showerror("Open failed", "Could not open the output in an editor.")

    def _start_move(self, e) -> None:
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _on_move(self, e) -> None:
        if self._drag:
            dx, dy = self._drag
            self.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def _animate(self) -> None:
        w = self.titlebar.winfo_width()
        h = self.titlebar.winfo_height()
        self._shimmer_t = (self._shimmer_t + 0.012) % 1.0
        sw = max(76, int(w * 0.20))
        sx = int(-120 + (w + 240) * self._shimmer_t)
        self.titlebar.coords(self._title_shimmer_id, sx, 0, sx + sw, h)
        self.titlebar.itemconfig(
            self._title_shimmer_id,
            fill=_blend("#1a1e34", "#27305a", 0.6 + 0.4 * math.sin(self._shimmer_t * math.tau)),
        )
        self._pulse_t = (self._pulse_t + 0.02) % 1.0
        t = (math.sin(self._pulse_t * math.tau) + 1.0) / 2.0
        color = _blend("#2a4fe2", "#3560ff", t * 0.6)
        try:
            self.btn_canvas.itemconfig(self._btn_rect, fill=color)
        except Exception:
            pass
        self.after(16, self._animate)

    def _bind_invisible_scroll(self, widget: tk.Text) -> None:
        def on_mousewheel(event):
            delta = event.delta
            if sys.platform == "darwin":
                units = int(-delta / 1) if delta else 0
            else:
                units = int(-delta / 120) if delta else 0
            if units == 0 and delta:
                units = -1 if delta > 0 else 1
            widget.yview_scroll(units, "units")
            return "break"

        def on_linux_up(_event):
            widget.yview_scroll(-3, "units")
            return "break"

        def on_linux_down(_event):
            widget.yview_scroll(3, "units")
            return "break"

        widget.bind("<MouseWheel>", on_mousewheel)
        widget.bind("<Button-4>", on_linux_up)
        widget.bind("<Button-5>", on_linux_down)

    def _layout_select_button(self, _evt=None) -> None:
        c = self.btn_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()

        cx, cy, r = w // 2, h // 2, 48
        for i in range(22, 0, -1):
            alpha = i / 22.0
            color = _blend("#0b0d12", "#1a1f38", alpha * 0.6)
            c.create_oval(cx - r - i, cy - r - i, cx + r + i, cy + r + i, outline=color, width=1)

        btn_w, btn_h = 240, 46
        x1, y1 = cx - btn_w // 2, cy - btn_h // 2
        x2, y2 = cx + btn_w // 2, cy + btn_h // 2
        radius = btn_h // 2

        self._btn_rect = self._round_rect(c, x1, y1, x2, y2, radius, fill="#2a4fe2", outline="#2d3570")
        self._btn_text = c.create_text(cx, cy, text="Select Folder", fill="#ffffff",
                                       font=("Segoe UI Semibold", 12))

        def enter(_): c.itemconfig(self._btn_rect, fill="#3560ff")
        def leave(_): c.itemconfig(self._btn_rect, fill="#2a4fe2")
        def click(_): self.select_folder_flow()

        for tag in (self._btn_rect, self._btn_text):
            c.tag_bind(tag, "<Enter>", enter)
            c.tag_bind(tag, "<Leave>", leave)
            c.tag_bind(tag, "<Button-1>", click)

    @staticmethod
    def _round_rect(c: tk.Canvas, x1: int, y1: int, x2: int, y2: int, r: int, **kw) -> int:
        points = [
            x1 + r, y1, x2 - r, y1,
            x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r,
            x1, y1 + r, x1, y1,
        ]
        return c.create_polygon(points, **kw, smooth=True)


# ─────────────────────────────────────────────────────────────────────────────
# Qt-safe launcher
# ─────────────────────────────────────────────────────────────────────────────

def on_select_folder() -> None:
    script = os.path.abspath(__file__)
    if not os.path.isfile(script):
        return
    args = [sys.executable, script, "--preset", "chatgpt"]
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(args, close_fds=True, creationflags=creationflags)
    except Exception:
        try:
            subprocess.Popen(args)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def _spawn_near_cursor(app: tk.Tk, w: int, h: int, x: Optional[int], y: Optional[int]) -> None:
    if x is not None and y is not None:
        sx, sy = x, y
    else:
        try:
            sx, sy = app.winfo_pointerx(), app.winfo_pointery()
        except Exception:
            sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
            sx, sy = sw // 2, sh // 2

    px = int(sx - w // 2)
    py = int(sy - h // 2)
    sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
    px = max(0, min(px, sw - w))
    py = max(0, min(py, sh - h))
    app.geometry(f"{w}x{h}+{px}+{py}")


def main() -> None:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--x", type=int, default=None)
    parser.add_argument("--y", type=int, default=None)
    parser.add_argument("--preset", type=str, default="chatgpt", help="chatgpt | flatabs | (empty)")
    parser.add_argument("--stdout", action="store_true", help="Headless: print to stdout and exit.")
    parser.add_argument("--folder", type=str, default=None, help="Headless: folder to scan.")
    parser.add_argument("--icon", type=str, default=None, help="Optional .ico or .png path (absolute or relative).")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent

    icon_path: Optional[Path] = None
    if args.icon:
        p = Path(args.icon)
        if not p.is_absolute():
            p = (base_dir / p).resolve()
        if p.exists():
            icon_path = p
    if icon_path is None:
        icon_path = find_app_icon(base_dir)

    if args.stdout and args.folder:
        opts = ScanOptions()
        p = (args.preset or "").strip().lower()
        if p == "flatabs":
            opts.format_mode = "flat"
            opts.absolute_paths = True
            opts.include_header = False
        else:
            opts.format_mode = "tree"
            opts.absolute_paths = False
            opts.include_header = True
            opts.use_common_excludes = True
            opts.max_items = 60_000
        result = scan_folder(args.folder, opts)
        sys.stdout.write(format_output(result, opts))
        return

    configure_windows_app_identity()
    app = TargetApp(preset=(args.preset or None), icon_path=icon_path)
    app.update_idletasks()

    try:
        g = app.geometry()
        w = int(g.split("x")[0])
        h = int(g.split("x")[1].split("+")[0])
    except Exception:
        w, h = 640, 410

    _spawn_near_cursor(app, w, h, args.x, args.y)
    app.mainloop()


if __name__ == "__main__":
    main()
