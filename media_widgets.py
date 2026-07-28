from __future__ import annotations

from pathlib import Path
from typing import Callable

import customtkinter as ctk
import tkinter as tk
from PIL import Image

from ui_theme import THEME, ColorPool


class GridPreview(ctk.CTkFrame):
    """Portrait preview with a theme-aware grid and optional image."""

    def __init__(
        self,
        master,
        width: int = 220,
        height: int = 320,
        **kwargs,
    ) -> None:
        super().__init__(master, width=width, height=height, **kwargs)
        THEME.bind(self, "media_slot")
        self.grid_propagate(False)

        self._width = width
        self._height = height
        self._image_path: Path | None = None
        self._photo: ctk.CTkImage | None = None

        self.canvas = tk.Canvas(
            self,
            width=width - 8,
            height=height - 8,
            highlightthickness=0,
            borderwidth=0,
        )
        self.canvas.place(relx=0.5, rely=0.5, anchor="center")
        THEME.subscribe(self._redraw)

    def destroy(self) -> None:
        THEME.unsubscribe(self._redraw)
        super().destroy()

    def set_image(self, path: str | Path | None) -> None:
        self._image_path = Path(path) if path else None
        self._redraw(THEME.colors)

    def _redraw(self, colors: ColorPool) -> None:
        if not self.winfo_exists():
            return

        canvas_width = max(10, self._width - 8)
        canvas_height = max(10, self._height - 8)
        self.canvas.configure(
            bg=colors.preview_background,
            width=canvas_width,
            height=canvas_height,
        )
        self.canvas.delete("all")

        if self._image_path and self._image_path.exists():
            try:
                image = Image.open(self._image_path).convert("RGBA")
                image.thumbnail((canvas_width, canvas_height), Image.Resampling.LANCZOS)
                self._photo = ctk.CTkImage(
                    light_image=image,
                    dark_image=image,
                    size=image.size,
                )
                self.canvas.create_image(
                    canvas_width // 2,
                    canvas_height // 2,
                    image=self._photo._dark_image,
                    anchor="center",
                )
                return
            except Exception:
                self._photo = None

        spacing = 22
        for x in range(0, canvas_width + 1, spacing):
            self.canvas.create_line(
                x, 0, x, canvas_height, fill=colors.grid_line
            )
        for y in range(0, canvas_height + 1, spacing):
            self.canvas.create_line(
                0, y, canvas_width, y, fill=colors.grid_line
            )


class WaveformBackdrop(tk.Canvas):
    """Subtle decorative waveform using the same global color pool."""

    def __init__(self, master, **kwargs) -> None:
        super().__init__(
            master,
            highlightthickness=0,
            borderwidth=0,
            **kwargs,
        )
        THEME.subscribe(self._redraw)
        self.bind("<Configure>", lambda _event: self._redraw(THEME.colors))

    def destroy(self) -> None:
        THEME.unsubscribe(self._redraw)
        super().destroy()

    def _redraw(self, colors: ColorPool) -> None:
        if not self.winfo_exists():
            return

        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        self.configure(bg=colors.card_background)
        self.delete("all")

        spacing = 26
        for x in range(0, width + 1, spacing):
            self.create_line(x, 0, x, height, fill=colors.grid_line)
        for y in range(0, height + 1, spacing):
            self.create_line(0, y, width, y, fill=colors.grid_line)

        middle = height // 2
        points: list[float] = []
        amplitudes = [5, 9, 15, 24, 37, 54, 36, 22, 14, 8, 5]
        if width < 100:
            return

        step = width / (len(amplitudes) - 1)
        for index, amplitude in enumerate(amplitudes):
            x = index * step
            y = middle - amplitude
            points.extend((x, y))

        reversed_points: list[float] = []
        for index, amplitude in reversed(list(enumerate(amplitudes))):
            x = index * step
            y = middle + amplitude
            reversed_points.extend((x, y))

        polygon = points + reversed_points
        self.create_polygon(
            polygon,
            fill=colors.accent_soft,
            outline=colors.accent,
            width=1,
            smooth=True,
        )
        self.create_line(
            0,
            middle,
            width,
            middle,
            fill=colors.accent,
            width=1,
        )


class ImageSlotCard(ctk.CTkFrame):
    """Reusable image card used by every Images-tab slot."""

    def __init__(
        self,
        master,
        title: str,
        select_callback: Callable[[], None],
        clear_callback: Callable[[], None],
    ) -> None:
        super().__init__(master)
        THEME.bind(self, "media_card")

        self.grid_columnconfigure(0, weight=1)
        self._path: Path | None = None
        self._thumbnail: ctk.CTkImage | None = None

        self.title_label = ctk.CTkLabel(
            self,
            text=title,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        THEME.bind(self.title_label, "primary_text")
        self.title_label.grid(row=0, column=0, padx=8, pady=(10, 6))

        self.preview_frame = ctk.CTkFrame(
            self,
            width=145,
            height=160,
        )
        THEME.bind(self.preview_frame, "media_slot")
        self.preview_frame.grid(row=1, column=0, padx=10, pady=0)
        self.preview_frame.grid_propagate(False)

        self.preview_button = ctk.CTkButton(
            self.preview_frame,
            text="Select Image",
            command=select_callback,
            width=125,
            height=138,
        )
        THEME.bind(self.preview_button, "button")
        self.preview_button.place(relx=0.5, rely=0.5, anchor="center")

        self.clear_button = ctk.CTkButton(
            self,
            text="⌫  Clear",
            command=clear_callback,
            width=70,
            height=28,
        )
        THEME.bind(self.clear_button, "button")
        self.clear_button.grid(row=2, column=0, pady=10)

    def set_image(self, path: str | Path | None) -> None:
        self._path = Path(path) if path else None

        if not self._path or not self._path.exists():
            self._thumbnail = None
            self.preview_button.configure(text="Select Image", image=None)
            return

        try:
            image = Image.open(self._path).convert("RGBA")
            image.thumbnail((118, 136), Image.Resampling.LANCZOS)
            self._thumbnail = ctk.CTkImage(
                light_image=image,
                dark_image=image,
                size=image.size,
            )
            self.preview_button.configure(
                text="",
                image=self._thumbnail,
            )
        except Exception:
            self._thumbnail = None
            self.preview_button.configure(text="Invalid Image", image=None)
