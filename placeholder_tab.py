from __future__ import annotations

import customtkinter as ctk

from ui_theme import THEME


class PlaceholderTab(ctk.CTkFrame):
    def __init__(self, master, title: str) -> None:
        super().__init__(master)
        THEME.bind(self, "window")

        card = ctk.CTkFrame(self, width=440, height=200)
        THEME.bind(card, "media_card")
        card.place(relx=0.5, rely=0.5, anchor="center")
        card.grid_propagate(False)

        heading = ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(size=24, weight="bold"),
        )
        THEME.bind(heading, "primary_text")
        heading.place(relx=0.5, rely=0.42, anchor="center")

        detail = ctk.CTkLabel(
            card,
            text="Placeholder screen using the same shared color pool.",
        )
        THEME.bind(detail, "secondary_text")
        detail.place(relx=0.5, rely=0.62, anchor="center")
