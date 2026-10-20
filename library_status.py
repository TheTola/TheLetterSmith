"""Stable library badge colors, independent of the application's theme."""
from __future__ import annotations


# Light text on dark surfaces keeps the meaning and contrast in every theme.
BADGE_COLORS = {
    "example": ("#ffe6a0", "#2d2512", "#8d7330"),
    "stock": ("#e6d4ff", "#271b38", "#7b52a8"),
    "draft": ("#e0e3e7", "#30343b", "#69717d"),
    "local": ("#9af0df", "#12352f", "#328879"),
    "github": ("#b7d9ff", "#142d50", "#528ed0"),
    "published": ("#a2efb9", "#153522", "#479965"),
    "different": ("#ffe0a0", "#3e2e12", "#ba8733"),
    "error": ("#ffc2c2", "#451e24", "#c5636c"),
}


def badge_style(kind: str) -> str:
    foreground, background, border = BADGE_COLORS[kind]
    return (
        f"color:{foreground};background:{background};border:1px solid {border};"
        "border-radius:7px;padding:1px 6px;font-weight:600;"
    )


def local_badges(entry) -> tuple[tuple[str, str], tuple[str, str]]:
    """Location/type and publication are independent facts."""
    if entry.example:
        location = ("Example", "example")
    elif entry.stock:
        location = ("Stock letter", "stock")
    elif entry.canonical:
        location = ("Saved draft", "draft")
    else:
        location = ("Local", "local")
    if not entry.valid:
        publication = ("Files damaged", "error")
    elif entry.expired:
        publication = ("Expired", "different")
    elif entry.canonical and "not published" in entry.saved_publication_state:
        publication = ("Published · Local changes", "different")
    elif entry.published:
        publication = ("Published", "published")
    elif entry.published_url:
        publication = ("Publication unverified", "draft")
    else:
        publication = ("Unpublished", "draft")
    return location, publication
