"""Shared helpers for concise, accessible control help."""

from __future__ import annotations

from PySide6 import QtGui, QtWidgets


def _help_text(text: str) -> str:
    normalized = " ".join(str(text).split())
    if not normalized:
        raise ValueError("help text must not be empty")
    return normalized


def set_control_help(
    widget: QtWidgets.QWidget,
    text: str,
    *,
    accessible_name: str | None = None,
) -> QtWidgets.QWidget:
    """Apply the same help text across Qt's control-help surfaces."""
    help_text = _help_text(text)
    widget.setToolTip(help_text)
    widget.setStatusTip(help_text)
    widget.setWhatsThis(help_text)
    if accessible_name is not None:
        widget.setAccessibleName(_help_text(accessible_name))
    return widget


def set_action_help(action: QtGui.QAction, text: str) -> QtGui.QAction:
    """Apply consistent help text to a menu or toolbar action."""
    help_text = _help_text(text)
    action.setToolTip(help_text)
    action.setStatusTip(help_text)
    action.setWhatsThis(help_text)
    return action


def set_tab_help(
    tabbar: QtWidgets.QTabBar,
    index: int,
    text: str,
) -> QtWidgets.QTabBar:
    """Apply hover and extended help to one tab-bar entry."""
    help_text = _help_text(text)
    tabbar.setTabToolTip(int(index), help_text)
    tabbar.setTabWhatsThis(int(index), help_text)
    return tabbar


__all__ = [
    "set_action_help",
    "set_control_help",
    "set_tab_help",
]
