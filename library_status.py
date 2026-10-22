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
    else:
        location = ("Local", "local")
    if entry.example or entry.stock:
        publication = ("", "")
    elif not entry.valid:
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


def set_card_status(card, location, publication):
    """Keep status accessible on hover without taking space from the cover."""
    from PySide6 import QtCore, QtWidgets
    card.storage_status = location
    card.publication_status = publication
    if not hasattr(card, "publication_indicator"):
        card.publication_indicator = QtWidgets.QLabel(card)
        card.publication_indicator.setProperty("themeIndependent", True)
        card.publication_indicator.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        card.publication_indicator.setFixedSize(12, 12)
        card.publication_indicator.move(card.width() - 23, 13)
    indicator = card.publication_indicator
    visible = bool(publication[0] and publication[1] not in ("draft", ""))
    indicator.setVisible(visible)
    if visible:
        indicator.setStyleSheet(f"background:{BADGE_COLORS[publication[1]][0]};border:2px solid #111b23;border-radius:6px;")
    indicator.setAccessibleName(publication[0])
    indicator.raise_()
    identity = card.entry if hasattr(card, "entry") else card.project
    description = "\n".join(filter(None, (identity.title, identity.recipient,
        location[0], publication[0], getattr(identity, "warning", ""))))
    card.setToolTip(description)
    card.setAccessibleName(description.replace("\n", ". "))
    card.update()


def paint_card_status(card):
    from PySide6 import QtCore, QtGui
    location = getattr(card, "storage_status", ("Local", "local"))
    painter = QtGui.QPainter(card)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    colors = ("local", "github") if location[1] == "both" else (location[1],)
    width = (card.width() - 14) / len(colors)
    for index, color in enumerate(colors):
        painter.setPen(QtGui.QPen(QtGui.QColor(BADGE_COLORS[color][2]), 3))
        painter.drawLine(QtCore.QPointF(7 + index * width, card.height() - 2),
                         QtCore.QPointF(7 + (index + 1) * width, card.height() - 2))
