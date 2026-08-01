# File: Over_Nexus.py
# Purpose: Nexus tab helper wiring.
#
# Current behavior:
# - No hidden Wall/Helper overlay buttons.
# - The Message details area shows automatically on the Message tab.
# - The Message details area hides automatically when leaving the Message tab.
# - The wall/text-background image is handled from Image_tab.py.

from __future__ import annotations

from PySide6 import QtCore, QtWidgets


# ──────────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# Controller
# ──────────────────────────────────────────────────────────────────────────────

class OverNexusController(QtCore.QObject):
    """
    Wires small Nexus-level behavior.

    Tabs assumed:
        Images  = 0
        Sound   = 1
        Message = 2
        Forge   = 3
        Command = 4
    """

    def __init__(self, nexus: object):
        super().__init__(nexus)

        self.nexus = nexus

        self.nexus.tabbar.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(self.nexus.tabbar.currentIndex())

    # ──────────────────────────────────────────────────────────────────
    # ──────────────────────────────────────────────────────────────────
    # ──────────────────────────────────────────────────────────────────
    # Automatic Message tab behavior
    # ──────────────────────────────────────────────────────────────────
    def _on_tab_changed(self, idx: int) -> None:
        is_message = idx == 2

        self._set_message_details_visible(is_message)

    def _set_message_details_visible(self, visible: bool) -> None:
        message_tab = getattr(self.nexus, "message_tab", None)
        if message_tab is None:
            return

        details = getattr(message_tab, "title_recipient_container", None)
        if not isinstance(details, QtWidgets.QWidget):
            details = getattr(message_tab, "title_sister_container", None)
        if isinstance(details, QtWidgets.QWidget):
            details.setVisible(visible)


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def install_over_nexus(
    nexus: object,
) -> OverNexusController:
    return OverNexusController(nexus)
