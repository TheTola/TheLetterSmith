"""Live previews for the editor's named text style menu and saved slots."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontInfo
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from named_text_styles import NamedStyleSet, StyleDefinition


def _available_font(font: QFont, requested_family: str) -> QFont:
    """Pick a display fallback without changing the saved requested family."""
    font.setFamily(requested_family)
    resolved = QFontInfo(font).family()
    if resolved:
        font.setFamily(resolved)
    return font


def preview_font(definition: StyleDefinition, base_font: QFont) -> QFont:
    """Preview style appearance at the UI font size, independent of document size."""
    font = QFont(base_font)
    font.setWeight(QFont.Weight(definition.font_weight))
    font.setItalic(definition.italic)
    font.setUnderline(definition.underline)
    font.setStrikeOut(definition.strikethrough)
    return _available_font(font, definition.font_family)


def preview_font_stylesheet(font: QFont) -> str:
    """Keep preview typography authoritative over inherited theme styles."""
    family = font.family().replace("\\", "\\\\").replace("'", "\\'")
    size = (
        f"font-size:{font.pointSizeF():g}pt;" if font.pointSizeF() > 0
        else f"font-size:{font.pixelSize()}px;" if font.pixelSize() > 0 else ""
    )
    decoration = " ".join(
        value for enabled, value in (
            (font.underline(), "underline"), (font.strikeOut(), "line-through"),
        ) if enabled
    ) or "none"
    return (
        f"font-family:'{family}';{size}font-weight:{int(font.weight())};"
        f"font-style:{'italic' if font.italic() else 'normal'};"
        f"text-decoration:{decoration};"
    )


class _StylePreviewRow(QWidget):
    def __init__(self, text: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setMinimumWidth(195)
        self.setMinimumHeight(32)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 3, 9, 3)
        layout.setSpacing(12)
        self.label = QLabel(text, self)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.label)
        layout.addStretch(1)
        arrow = QLabel("▸", self)
        arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(arrow)


class StyleSelectionMenu(QMenu):
    """Apply a style from its label; retain the arrow for its options submenu."""

    def _apply_action(self, action) -> bool:
        if action is None or not hasattr(action, "_named_text_preview_row"):
            return False
        self.close()
        action.trigger()
        return True

    def mouseReleaseEvent(self, event) -> None:
        action = self.actionAt(event.position().toPoint())
        if (event.button() == Qt.LeftButton and action is not None
                and event.position().x() < self.actionGeometry(action).right() - 28
                and self._apply_action(action)):
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and self._apply_action(self.activeAction())):
            event.accept()
            return
        super().keyPressEvent(event)


def add_style_preview_submenu(menu: QMenu, label: str) -> QMenu:
    """Add a native submenu with an individually styled label."""
    submenu = QMenu(label, menu)
    action = QWidgetAction(menu)
    action.setText(label)
    row = _StylePreviewRow(label, menu)
    action.setDefaultWidget(row)
    action.setMenu(submenu)
    action._named_text_preview_row = row
    menu.addAction(action)
    return submenu


def set_action_style_preview(
    action: QWidgetAction, definition: StyleDefinition
) -> None:
    """Display the action's exact name in its definition's font and color."""
    row = getattr(action, "_named_text_preview_row", None)
    if row is None:
        raise ValueError("Action is not a named text style preview")
    font = preview_font(definition, row.label.font())
    row.label.setFont(font)
    color = QColor(definition.font_color).name(QColor.NameFormat.HexArgb)
    row.label.setStyleSheet(
        f"color: {color}; background: transparent; {preview_font_stylesheet(font)}"
    )
    row.updateGeometry()
    row.update()


def set_slot_style_preview(
    button: QAbstractButton, saved_set: NamedStyleSet | None
) -> None:
    """Show a saved set's Heading 1 family and color on its Style N button."""
    if getattr(button, "_named_text_preview_base_font", None) is None:
        button._named_text_preview_base_font = QFont(button.font())
        button._named_text_preview_base_stylesheet = button.styleSheet()
    if saved_set is None:
        button.setFont(button._named_text_preview_base_font)
        button.setStyleSheet(button._named_text_preview_base_stylesheet)
        return
    definition = saved_set.get("heading_1")
    font = _available_font(
        QFont(button._named_text_preview_base_font), definition.font_family
    )
    button.setFont(font)
    selector = "QToolButton" if isinstance(button, QToolButton) else "QPushButton"
    color = QColor(definition.font_color).name(QColor.NameFormat.HexArgb)
    button.setStyleSheet(
        button._named_text_preview_base_stylesheet
        + f"\n{selector}, {selector}:hover, {selector}:pressed"
        f" {{ color: {color}; {preview_font_stylesheet(font)} }}"
    )
