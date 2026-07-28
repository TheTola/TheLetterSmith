from __future__ import annotations

from tkinter import colorchooser, messagebox

import customtkinter as ctk

from images_tab import ImagesTab
from placeholder_tab import PlaceholderTab
from sound_tab import SoundTab
from ui_theme import THEME


class LettersmithApp(ctk.CTk):
    TAB_NAMES = ("Images", "Sound", "Message", "Forge", "Command")

    def __init__(self) -> None:
        super().__init__()

        ctk.set_appearance_mode("dark")
        self.title("The Silver-Tongued Lettersmith")
        self.geometry("1280x760")
        self.minsize(960, 640)
        THEME.bind(self, "window")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._tab_buttons: dict[str, ctk.CTkButton] = {}
        self._underline_frames: dict[str, ctk.CTkFrame] = {}
        self._tabs: dict[str, ctk.CTkFrame] = {}
        self._active_tab = "Images"

        self._build_header()
        self._build_tabs()
        self.show_tab("Images")

        self.bind("<Control-Shift-C>", lambda _event: self.choose_accent())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, height=64)
        THEME.bind(header, "transparent")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        title_label = ctk.CTkLabel(
            header,
            text="The Silver-Tongued Lettersmith",
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        THEME.bind(title_label, "accent_text")
        title_label.grid(row=0, column=0, padx=(12, 30), pady=(10, 0), sticky="w")

        navigation = ctk.CTkFrame(header)
        THEME.bind(navigation, "transparent")
        navigation.grid(row=0, column=1, sticky="ew", padx=15)
        for index in range(len(self.TAB_NAMES)):
            navigation.grid_columnconfigure(index, weight=1)

        for index, name in enumerate(self.TAB_NAMES):
            cell = ctk.CTkFrame(navigation)
            THEME.bind(cell, "transparent")
            cell.grid(row=0, column=index, sticky="ew")
            cell.grid_columnconfigure(0, weight=1)

            button = ctk.CTkButton(
                cell,
                text=name,
                height=38,
                command=lambda tab_name=name: self.show_tab(tab_name),
            )
            THEME.bind(button, "inactive_tab")
            button.grid(row=0, column=0, sticky="ew")
            self._tab_buttons[name] = button

            underline = ctk.CTkFrame(cell, height=2)
            THEME.bind(underline, "accent_line")
            underline.grid(row=1, column=0, sticky="ew")
            underline.grid_remove()
            self._underline_frames[name] = underline

        settings_button = ctk.CTkButton(
            header,
            text="⚙",
            width=38,
            height=38,
            command=self.choose_accent,
        )
        THEME.bind(settings_button, "button")
        settings_button.grid(row=0, column=2, padx=(10, 12), pady=(8, 0))

    def _build_tabs(self) -> None:
        host = ctk.CTkFrame(self)
        THEME.bind(host, "window")
        host.grid(row=1, column=0, sticky="nsew")
        host.grid_columnconfigure(0, weight=1)
        host.grid_rowconfigure(0, weight=1)

        self._tabs["Images"] = ImagesTab(host)
        self._tabs["Sound"] = SoundTab(host)
        self._tabs["Message"] = PlaceholderTab(host, "Message")
        self._tabs["Forge"] = PlaceholderTab(host, "Forge")
        self._tabs["Command"] = PlaceholderTab(host, "Command")

        for tab in self._tabs.values():
            tab.grid(row=0, column=0, sticky="nsew")

    def show_tab(self, name: str) -> None:
        if name not in self._tabs:
            return

        self._active_tab = name
        self._tabs[name].tkraise()

        for tab_name, button in self._tab_buttons.items():
            role = "active_tab" if tab_name == name else "inactive_tab"
            THEME.bind(button, role)

            underline = self._underline_frames[tab_name]
            if tab_name == name:
                underline.grid()
            else:
                underline.grid_remove()

    def choose_accent(self) -> None:
        selected = colorchooser.askcolor(
            color=THEME.colors.accent,
            title="Choose Shared Accent Color",
            parent=self,
        )
        color = selected[1]
        if color:
            THEME.set_accent(color)

    def _on_close(self) -> None:
        try:
            sound_tab = self._tabs.get("Sound")
            if sound_tab is not None:
                sound_tab.destroy()
        finally:
            self.destroy()


def run() -> None:
    app = LettersmithApp()
    app.mainloop()
