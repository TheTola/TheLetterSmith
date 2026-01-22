# File: target.py — Professional, animated & vibrant (no features removed)
# Core behavior preserved:
#   - Always scans BOTH files and folders recursively
#   - One-click "Select Folder" entry point
#   - Tiny borderless prompt with "Nevermind" and "Copy" after scan
#
# Enhancements (strictly additive, no removals):
#   - Custom minimal title bar (only "–" and "×"), draggable, with subtle shimmer
#   - Animated "Select Folder" button (breathing/pulse)
#   - Non-blocking scan (threaded) + spinner overlay while scanning (no logs, no cancel)
#   - Micro "confetti" flourish on successful Copy
#   - NEW: Spawns at the mouse cursor on launch (or at --x/--y if provided)
#
# Standard library only.

import os
import sys
import threading
import time
import math
import random
import argparse
import tkinter as tk
from tkinter import filedialog, messagebox


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))

def _lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * _clamp01(t)))

def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"

def _blend(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex(_lerp(r1, r2, t), _lerp(g1, g2, t), _lerp(b1, b2, t))


# ─────────────────────────────────────────────────────────────────────────────
# Core: recursive listing — includes BOTH directories and files (always)
# ─────────────────────────────────────────────────────────────────────────────

def list_directory(root_path: str) -> list[str]:
    """
    Recursively list all folders AND files under root_path.
    Uses os.walk with onerror guard; returns absolute paths.
    """
    out: list[str] = []
    def _onerror(_e):  # swallow per-entry errors (permissions, etc.)
        pass
    for r, dirs, files in os.walk(root_path, topdown=True, onerror=_onerror):
        for d in dirs:
            out.append(os.path.join(r, d))
        for f in files:
            out.append(os.path.join(r, f))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Animated Spinner Overlay (during scan)
# ─────────────────────────────────────────────────────────────

class SpinnerOverlay(tk.Toplevel):
    def __init__(self, parent: tk.Tk):
        super().__init__(parent)
        self.overrideredirect(True)
        self.configure(bg="#000000")
        self.attributes("-alpha", 0.0)  # fade in
        self.transient(parent)
        self.lift(parent)
        self.canvas = tk.Canvas(self, width=180, height=120, bg="#111318",
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._angle = 0.0
        self._running = True

        # Center over parent
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        w, h = 180, 120
        x, y = px + (pw - w)//2, py + (ph - h)//2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # Fade-in animation
        self._fade_in_step = 0
        self.after(10, self._fade_in)

        # Start spinner animation
        self.after(0, self._tick)

    def _fade_in(self):
        if self._fade_in_step <= 10:
            self.attributes("-alpha", self._fade_in_step / 10.0 * 0.96 + 0.04)
            self._fade_in_step += 1
            self.after(16, self._fade_in)

    def _tick(self):
        if not self._running:
            return
        self._angle = (self._angle + 10) % 360
        self._draw()
        self.after(60, self._tick)

    def _draw(self):
        self.canvas.delete("all")
        w = int(self.canvas["width"])
        h = int(self.canvas["height"])
        cx, cy = w // 2, h // 2

        # Halo disk
        self.canvas.create_oval(cx-56, cy-56, cx+56, cy+56,
                                outline="#1c2340", width=3)
        # Spinner spokes
        spokes = 12
        radius = 42
        for i in range(spokes):
            a = math.radians(self._angle + (360/spokes) * i)
            x1 = cx + math.cos(a) * (radius - 10)
            y1 = cy + math.sin(a) * (radius - 10)
            x2 = cx + math.cos(a) * radius
            y2 = cy + math.sin(a) * radius
            alpha = (i + (self._angle/360)*spokes) % spokes / (spokes - 1)
            color = _blend("#3357ff", "#66b5ff", alpha)  # blue gradient sweep
            self.canvas.create_line(x1, y1, x2, y2, fill=color, width=3, capstyle="round")

        self.canvas.create_text(cx, cy+40, text="Scanning…",
                                fill="#e7eaf2", font=("Segoe UI", 10))

    def close(self):
        self._running = False
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Tiny Copy Prompt (borderless, only "Nevermind" and "Copy")
# ─────────────────────────────────────────────────────────────────────────────

class TinyCopyPrompt(tk.Toplevel):
    def __init__(self, parent: tk.Tk, entries: list[str]):
        super().__init__(parent)
        self.entries = entries
        self.overrideredirect(True)
        self.configure(bg="#111318")
        self.transient(parent)
        self.lift(parent)

        # Position near parent center
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        w, h = 260, 110
        x, y = px + (pw - w)//2, py + (ph - h)//2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # Dragging
        self._drag = None

        # Content
        body = tk.Canvas(self, width=w, height=h, bg="#111318",
                         highlightthickness=0, bd=0)
        body.pack(fill="both", expand=True)

        # Rounded rectangle backdrop
        def roundrect(x1, y1, x2, y2, r=12, **kw):
            points = [
                x1+r, y1, x2-r, y1,
                x2, y1, x2, y1+r,
                x2, y2-r, x2, y2,
                x2-r, y2, x1+r, y2,
                x1, y2, x1, y2-r,
                x1, y1+r, x1, y1,
            ]
            return body.create_polygon(points, **kw, smooth=True)
        roundrect(4, 4, w-4, h-4, r=14, fill="#0f121a", outline="#2a3150")

        body.create_text(w//2, 40,
            text=f"Found {len(entries):,} item(s)\nCopy to clipboard?",
            fill="#e7e8ea", font=("Segoe UI", 10), justify="center")

        # Buttons
        def mk_btn(x, y, text, bg, bgh, fg, cmd):
            btn_w, btn_h = 96, 28
            rect = body.create_rectangle(x, y, x+btn_w, y+btn_h,
                                         fill=bg, outline="#2c3458", width=1)
            label = body.create_text(x+btn_w/2, y+btn_h/2,
                                     text=text, fill=fg, font=("Segoe UI", 10))
            # hover
            def enter(_): body.itemconfig(rect, fill=bgh)
            def leave(_): body.itemconfig(rect, fill=bg)
            def click(_): cmd()
            body.tag_bind(rect, "<Enter>", enter)
            body.tag_bind(label, "<Enter>", enter)
            body.tag_bind(rect, "<Leave>", leave)
            body.tag_bind(label, "<Leave>", leave)
            body.tag_bind(rect, "<Button-1>", click)
            body.tag_bind(label, "<Button-1>", click)

        mk_btn(28, 68, "Nevermind", "#232838", "#2b3150", "#e6e6e6", self.destroy)
        mk_btn(w-28-96, 68, "Copy", "#2f6fe0", "#3b7cf0", "#ffffff", self._copy_all)

        # Drag to move tiny prompt
        def start_move(e):
            self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())
        def on_move(e):
            if self._drag:
                dx, dy = self._drag
                self.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")
        body.bind("<Button-1>", start_move)
        body.bind("<B1-Motion>", on_move)

        # Close on Escape
        self.bind("<Escape>", lambda e: self.destroy())

        # Grab focus (acts modal)
        self.grab_set()
        self.focus_force()

    def _copy_all(self):
        """
        Hardened clipboard copy:
          - Use the root window to own the clipboard
          - Retry briefly if clipboard is busy
          - Call update() before closing so ownership persists
        """
        entries = getattr(self, "entries", [])
        if not entries:
            try:
                messagebox.showwarning("Nothing to copy", "No paths to copy.")
            finally:
                self.destroy()
            return

        text = "\n".join(entries)

        # Get the root (master) window; safest owner for clipboard
        if isinstance(self.master, tk.Tk):
            owner = self.master
        else:
            # Fallback to a named parent widget
            owner = self.nametowidget(self.winfo_parent())

        # Try a few times in case clipboard is temporarily locked
        max_tries = 3
        for i in range(max_tries):
            try:
                owner.clipboard_clear()
                owner.clipboard_append(text)
                # Cement ownership so it survives closing this tiny window
                owner.update()
                # Optional micro-celebration
                try:
                    self._confetti_burst()
                except Exception:
                    pass
                # Close a hair later so the UI has time to process
                self.after(200, self.destroy)
                return
            except tk.TclError as e:
                # Brief backoff then retry
                self.after(60)
                if i == max_tries - 1:
                    try:
                        messagebox.showerror("Copy failed", f"{e}\n\nTip: Try pressing Copy again.")
                    finally:
                        self.destroy()
            except Exception as e:
                try:
                    messagebox.showerror("Copy failed", f"{type(e).__name__}: {e}")
                finally:
                    self.destroy()
                return

    def _confetti_burst(self):
        # Tiny, fast particles within the prompt area
        w = self.winfo_width()
        h = self.winfo_height()
        c = tk.Canvas(self, width=w, height=h, bg="", highlightthickness=0, bd=0)
        c.place(x=0, y=0)
        parts = []
        colors = ["#66b5ff", "#99d1ff", "#6ae6bf", "#ffd166", "#ff6aa6"]
        for _ in range(18):
            x = random.randint(30, w-30)
            y = random.randint(20, h-30)
            r = random.randint(2, 4)
            vx = random.uniform(-1.2, 1.2)
            vy = random.uniform(-2.2, -0.6)
            parts.append([c.create_oval(x-r, y-r, x+r, y+r, fill=random.choice(colors), width=0), x, y, vx, vy, 0])

        def tick():
            done = True
            for p in parts:
                item, x, y, vx, vy, t = p
                t += 1
                x += vx
                y += vy
                vy += 0.09  # gravity
                p[1], p[2], p[4], p[5] = x, y, vy, t
                c.move(item, vx, vy)
                if 0 <= x <= w and 0 <= y <= h and t < 22:
                    done = False
            if not done:
                self.after(16, tick)
            else:
                c.destroy()
        tick()


# ─────────────────────────────────────────────────────────────────────────────
# Custom Title Bar + Main App
# ─────────────────────────────────────────────────────────────────────────────

class MinimalApp(tk.Tk):
    def __init__(self):
        super().__init__()
        # Undecorated window; we draw our own minimal title bar
        self.overrideredirect(True)
        self.geometry("420x180")
        self.minsize(360, 160)
        self.configure(bg="#0b0d12")

        # Dragging vars
        self._drag = None
        self._shimmer_t = 0.0
        self._pulse_t = 0.0

        # Title bar
        self._build_titlebar()

        # Content area
        self.body = tk.Canvas(self, bg="#0b0d12", highlightthickness=0, bd=0)
        self.body.pack(fill="both", expand=True)
        self.body.bind("<Button-1>", self._start_move)
        self.body.bind("<B1-Motion>", self._on_move)

        # Animated/select button
        self._build_select_button()

        # Kick off subtle animations
        self.after(16, self._animate)

    # ——— Title bar ———
    def _build_titlebar(self):
        h = 34
        self.titlebar = tk.Canvas(self, height=h, bg="#141724", highlightthickness=0, bd=0)
        self.titlebar.pack(fill="x", side="top")

        # Shimmer gradient (animated)
        self._title_shimmer_id = self.titlebar.create_rectangle(0, 0, 0, h, fill="#1a1e34", width=0)
        self.titlebar.bind("<Button-1>", self._start_move)
        self.titlebar.bind("<B1-Motion>", self._on_move)

        # Minimize (–)
        self._btn_min_rect = self.titlebar.create_rectangle(0, 0, 0, 0, fill="#141724", outline="")
        self._btn_min_text = self.titlebar.create_text(0, 0, text="–", fill="#e6e6e6", font=("Segoe UI", 12))
        # Close (×)
        self._btn_close_rect = self.titlebar.create_rectangle(0, 0, 0, 0, fill="#141724", outline="")
        self._btn_close_text = self.titlebar.create_text(0, 0, text="×", fill="#e6e6e6", font=("Segoe UI Semibold", 12))

        self.titlebar.bind("<Configure>", self._layout_titlebar)
        # Interactions
        for tag in (self._btn_min_rect, self._btn_min_text):
            self.titlebar.tag_bind(tag, "<Button-1>", lambda e: self.iconify())
            self.titlebar.tag_bind(tag, "<Enter>", lambda e, r=self._btn_min_rect: self.titlebar.itemconfig(r, fill="#1a1f38"))
            self.titlebar.tag_bind(tag, "<Leave>", lambda e, r=self._btn_min_rect: self.titlebar.itemconfig(r, fill="#141724"))
        for tag in (self._btn_close_rect, self._btn_close_text):
            self.titlebar.tag_bind(tag, "<Button-1>", lambda e: self.destroy())
            self.titlebar.tag_bind(tag, "<Enter>", lambda e, r=self._btn_close_rect: self.titlebar.itemconfig(r, fill="#2a2f4d"))
            self.titlebar.tag_bind(tag, "<Leave>", lambda e, r=self._btn_close_rect: self.titlebar.itemconfig(r, fill="#141724"))

    def _layout_titlebar(self, _evt=None):
        w = self.titlebar.winfo_width()
        h = self.titlebar.winfo_height()
        # shimmer block sweeps horizontally in _animate()
        self.titlebar.coords(self._title_shimmer_id, -100, 0, 0, h)

        # Buttons on the right
        bx = w
        bw = 36
        by0, by1 = 0, h
        # Close box
        self.titlebar.coords(self._btn_close_rect, bx-bw, by0, bx, by1)
        self.titlebar.coords(self._btn_close_text, bx-bw/2, h/2)
        bx -= bw
        # Minimize
        self.titlebar.coords(self._btn_min_rect, bx-bw, by0, bx, by1)
        self.titlebar.coords(self._btn_min_text, bx-bw/2, h/2)

    # ——— Content ———
    def _build_select_button(self):
        # A pill button drawn on canvas so we can animate color smoothly
        self._btn_rect = None
        self._btn_text = None
        self.body.bind("<Configure>", self._layout_button)

    def _layout_button(self, _evt=None):
        self.body.delete("all")
        w = self.body.winfo_width()
        h = self.body.winfo_height()

        # Soft center glow ring
        cx, cy, r = w//2, h//2, 66
        for i in range(30, 0, -1):
            alpha = i / 30.0
            color = _blend("#0b0d12", "#1a1f38", alpha * 0.5)
            self.body.create_oval(cx-r-i, cy-r-i, cx+r+i, cy+r+i, outline=color, width=1)

        # Pill button geometry
        btn_w, btn_h = 180, 44
        x1, y1 = cx - btn_w//2, cy - btn_h//2
        x2, y2 = cx + btn_w//2, cy + btn_h//2
        radius = btn_h // 2

        # Draw pill
        self._btn_rect = self._round_rect(x1, y1, x2, y2, radius, fill="#2a4fe2", outline="#2d3570")
        self._btn_text = self.body.create_text(cx, cy, text="Select Folder",
                                               fill="#ffffff", font=("Segoe UI Semibold", 11))

        # Hover interactions
        def enter(_): self.body.itemconfig(self._btn_rect, fill="#3560ff")
        def leave(_): self.body.itemconfig(self._btn_rect, fill="#2a4fe2")
        def click(_): self._on_select_folder()
        for tag in (self._btn_rect, self._btn_text):
            self.body.tag_bind(tag, "<Enter>", enter)
            self.body.tag_bind(tag, "<Leave>", leave)
            self.body.tag_bind(tag, "<Button-1>", click)

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        points = [
            x1+r, y1, x2-r, y1,
            x2, y1, x2, y1+r,
            x2, y2-r, x2, y2,
            x2-r, y2, x1+r, y2,
            x1, y2, x1, y2-r,
            x1, y1+r, x1, y1,
        ]
        return self.body.create_polygon(points, **kw, smooth=True)

    # ——— Interactions ———
    def _start_move(self, e):
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _on_move(self, e):
        if self._drag:
            dx, dy = self._drag
            self.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def _on_select_folder(self):
        folder = filedialog.askdirectory(title="Select Folder")
        if not folder:
            return

        # Show spinner overlay & run scan on background thread
        overlay = SpinnerOverlay(self)
        entries_out: list[str] = []

        def worker():
            try:
                result = list_directory(folder)
                # force absolute paths for clarity
                result = [os.path.abspath(p) for p in result]
                entries_out.extend(result)
            finally:
                # Close spinner & show prompt in UI thread
                self.after(0, lambda: self._after_scan(overlay, entries_out))

        threading.Thread(target=worker, daemon=True).start()

    def _after_scan(self, overlay: SpinnerOverlay, entries: list[str]):
        try:
            overlay.close()
        except Exception:
            pass
        TinyCopyPrompt(self, entries)

    # ——— Animation loop ———
    def _animate(self):
        # Title shimmer sweeps left→right
        w = self.titlebar.winfo_width()
        h = self.titlebar.winfo_height()
        self._shimmer_t = (self._shimmer_t + 0.01) % 1.0
        sw = max(68, int(w * 0.18))
        sx = int(-100 + (w + 200) * self._shimmer_t)
        self.titlebar.coords(self._title_shimmer_id, sx, 0, sx + sw, h)
        self.titlebar.itemconfig(self._title_shimmer_id,
                                 fill=_blend("#1a1e34", "#27305a", 0.6 + 0.4*math.sin(self._shimmer_t*math.tau)))

        # Button gentle pulse
        self._pulse_t = (self._pulse_t + 0.02) % 1.0
        t = (math.sin(self._pulse_t * math.tau) + 1) / 2  # 0..1
        color = _blend("#2a4fe2", "#3560ff", t*0.6)
        if self._btn_rect is not None:
            self.body.itemconfig(self._btn_rect, fill=color)

        self.after(16, self._animate)


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # Parse optional coordinates. If not provided, we sample the system pointer via Tk.
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--x", type=int, default=None)
    parser.add_argument("--y", type=int, default=None)
    args, _ = parser.parse_known_args()

    app = MinimalApp()
    app.update_idletasks()  # finish initial layout to get accurate size

    # Use current window size (MinimalApp sets a default)
    try:
        # geometry format "WxH+X+Y"
        g = app.geometry()
        w = int(g.split("x")[0])
        h = int(g.split("x")[1].split("+")[0])
    except Exception:
        w, h = 420, 180

    # Determine target spawn point: prefer args; else current mouse cursor
    if args.x is not None and args.y is not None:
        sx, sy = args.x, args.y
    else:
        try:
            sx, sy = app.winfo_pointerx(), app.winfo_pointery()
        except Exception:
            # Fallback to screen center
            sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
            sx, sy = sw // 2, sh // 2

    # Position window so its CENTER is at (sx, sy); clamp to screen
    x = int(sx - w // 2)
    y = int(sy - h // 2)
    sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
    x = max(0, min(x, sw - w))
    y = max(0, min(y, sh - h))
    app.geometry(f"{w}x{h}+{x}+{y}")

    app.mainloop()

if __name__ == "__main__":
    main()
