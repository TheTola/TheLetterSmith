from __future__ import annotations

from typing import Mapping

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from curtain_color import (
    CurtainDisplayColors,
    RGB,
    curtain_display_palette,
    curtain_variant_rgbs,
)
from settings_store import (
    CURTAIN_STYLE_LABELS,
    CURTAIN_STYLE_OPTIONS,
    DEFAULT_CURTAIN_STYLE,
    VALID_CURTAIN_STYLES,
    SettingsStore,
    normalize_curtain_style,
)
from ui_theme import CYBER_FORGE_THEME, ThemeTokens


CURTAIN_STYLE_ROLE = int(Qt.UserRole) + 1
CURTAIN_BACKGROUND_ROLE = int(Qt.UserRole) + 2
CURTAIN_FOREGROUND_ROLE = int(Qt.UserRole) + 3

_DIRECT_STYLES = (
    "pure_white",
    "average_color",
    "complementary_average_color",
)
_LIGHT_STYLES = ("normal_light", "complementary_light")
_DARK_STYLES = ("normal_dark", "complementary_dark")


def _qcolor(value: object, fallback: str) -> QtGui.QColor:
    color = QtGui.QColor(value)
    return color if color.isValid() else QtGui.QColor(fallback)


class CurtainStyleModel(QtGui.QStandardItemModel):
    """One seven-leaf color model shared by every curtain selector."""

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._variants: dict[str, RGB] = curtain_variant_rgbs(None)
        self._display_palette: dict[str, CurtainDisplayColors] = {}
        for style, label in CURTAIN_STYLE_OPTIONS:
            item = QtGui.QStandardItem(label)
            item.setEditable(False)
            item.setData(style, CURTAIN_STYLE_ROLE)
            self.appendRow(item)
        self.set_preview_colors({})

    @property
    def variants(self) -> dict[str, RGB]:
        return dict(self._variants)

    def display_colors(self, style: object) -> CurtainDisplayColors:
        return self._display_palette[normalize_curtain_style(style)]

    def row_for_style(self, style: object) -> int:
        normalized = normalize_curtain_style(style)
        for row in range(self.rowCount()):
            if self.index(row, 0).data(CURTAIN_STYLE_ROLE) == normalized:
                return row
        return -1

    def set_preview_colors(
        self,
        colors: Mapping[str, tuple[int, int, int]],
    ) -> None:
        variants = curtain_variant_rgbs(None)
        for raw_style, raw_rgb in colors.items():
            style = str(raw_style or "").strip()
            if style not in VALID_CURTAIN_STYLES:
                continue
            try:
                if len(raw_rgb) != 3:
                    continue
                rgb = tuple(
                    max(0, min(255, int(channel)))
                    for channel in raw_rgb
                )
            except (TypeError, ValueError):
                continue
            variants[style] = rgb  # type: ignore[assignment]

        palette = curtain_display_palette(variants)
        blocker = QtCore.QSignalBlocker(self)
        for row in range(self.rowCount()):
            item = self.item(row)
            style = str(item.data(CURTAIN_STYLE_ROLE))
            display = palette[style]
            item.setData(
                QtGui.QColor(*display.background),
                CURTAIN_BACKGROUND_ROLE,
            )
            item.setData(
                QtGui.QColor(*display.foreground),
                CURTAIN_FOREGROUND_ROLE,
            )
        del blocker
        self._variants = variants
        self._display_palette = palette
        if self.rowCount():
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.rowCount() - 1, 0),
                [CURTAIN_BACKGROUND_ROLE, CURTAIN_FOREGROUND_ROLE],
            )


class CurtainStyleDelegate(QtWidgets.QStyledItemDelegate):
    """Paint one curtain choice with its actual calculated colors."""

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.apply_theme_tokens(CYBER_FORGE_THEME.tokens)

    def apply_theme_tokens(self, colors: ThemeTokens) -> None:
        self._hover_outline = _qcolor(colors.accent, "#4aa3c2")
        self._selected_outline = _qcolor(colors.highlight, "#ffffff")
        self._fallback_background = _qcolor(
            colors.control_background,
            "#202830",
        )
        self._fallback_foreground = _qcolor(colors.text, "#ffffff")

    def sizeHint(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtCore.QSize:
        del option, index
        return QtCore.QSize(270, 40)

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        background = index.data(CURTAIN_BACKGROUND_ROLE)
        foreground = index.data(CURTAIN_FOREGROUND_ROLE)
        if not isinstance(background, QtGui.QColor) or not background.isValid():
            background = self._fallback_background
        if not isinstance(foreground, QtGui.QColor) or not foreground.isValid():
            foreground = self._fallback_foreground

        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = QtCore.QRectF(option.rect.adjusted(2, 2, -2, -2))
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, 5.0, 5.0)
        painter.fillPath(path, background)
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        hovered = bool(option.state & QtWidgets.QStyle.State_MouseOver)
        if selected or hovered:
            painter.setPen(
                QtGui.QPen(
                    self._selected_outline if selected else self._hover_outline,
                    2.0 if selected else 1.0,
                )
            )
            painter.drawPath(path)
        font = QtGui.QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(foreground)
        painter.drawText(
            option.rect.adjusted(12, 2, -34, -2),
            Qt.AlignLeft | Qt.AlignVCenter,
            str(index.data(Qt.DisplayRole) or ""),
        )
        if selected:
            painter.drawText(
                option.rect.adjusted(option.rect.width() - 31, 2, -9, -2),
                Qt.AlignCenter,
                "\N{CHECK MARK}",
            )
        painter.restore()


class _CurtainMenuRow(QtWidgets.QAbstractButton):
    """A QWidgetAction row that preserves the exact calculated colors."""

    def __init__(
        self,
        style: str,
        model: CurtainStyleModel,
        activated: object,
        parent: QtWidgets.QWidget,
    ) -> None:
        super().__init__(parent)
        self._style = style
        self._model = model
        self._activated = activated
        self._current_style = DEFAULT_CURTAIN_STYLE
        self._delegate = CurtainStyleDelegate(self)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(CURTAIN_STYLE_LABELS.get(style, style))
        self.clicked.connect(self._activate)

    @property
    def curtain_style(self) -> str:
        return self._style

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(270, 40)

    def set_current_style(self, style: str) -> None:
        self._current_style = normalize_curtain_style(style)
        self.update()

    def apply_theme_tokens(self, colors: ThemeTokens) -> None:
        self._delegate.apply_theme_tokens(colors)
        self.update()

    @QtCore.Slot()
    def _activate(self) -> None:
        self._activated(self._style)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        row = self._model.row_for_style(self._style)
        if row < 0:
            return
        option = QtWidgets.QStyleOptionViewItem()
        option.initFrom(self)
        option.rect = self.rect()
        if self.underMouse() or self.hasFocus():
            option.state |= QtWidgets.QStyle.State_MouseOver
        if self._current_style == self._style:
            option.state |= QtWidgets.QStyle.State_Selected
        painter = QtGui.QPainter(self)
        self._delegate.paint(painter, option, self._model.index(row, 0))


class _CurtainChoiceMenu(QtWidgets.QMenu):
    """Hierarchical seven-leaf curtain menu backed by a shared model."""

    styleActivated = QtCore.Signal(str)
    previewColorsRequested = QtCore.Signal()

    def __init__(
        self,
        title: str,
        parent: QtWidgets.QWidget | None,
        *,
        object_name: str,
    ) -> None:
        super().__init__(title, parent)
        self.setObjectName(object_name)
        self.setToolTipsVisible(True)
        self._model: CurtainStyleModel | None = None
        self._current_style = DEFAULT_CURTAIN_STYLE
        self._theme_tokens = CYBER_FORGE_THEME.tokens
        self._leaf_actions: dict[str, QtWidgets.QWidgetAction] = {}
        self._leaf_rows: dict[str, _CurtainMenuRow] = {}
        self.light_menu: QtWidgets.QMenu | None = None
        self.dark_menu: QtWidgets.QMenu | None = None
        self.aboutToShow.connect(self._before_show)

    def set_model(self, model: CurtainStyleModel) -> None:
        if model is self._model:
            self._refresh_rows()
            return
        if self._model is not None:
            for signal, callback in (
                (self._model.dataChanged, self._refresh_rows),
                (self._model.modelReset, self._rebuild),
                (self._model.rowsInserted, self._rebuild),
                (self._model.rowsRemoved, self._rebuild),
            ):
                try:
                    signal.disconnect(callback)
                except (RuntimeError, TypeError):
                    pass
        self._model = model
        model.dataChanged.connect(self._refresh_rows)
        model.modelReset.connect(self._rebuild)
        model.rowsInserted.connect(self._rebuild)
        model.rowsRemoved.connect(self._rebuild)
        self._rebuild()

    def set_current_style(self, style: str) -> None:
        self._current_style = normalize_curtain_style(style)
        self._refresh_rows()

    def apply_theme_tokens(self, colors: ThemeTokens) -> None:
        self._theme_tokens = colors
        menu_qss = (
            "QMenu{"
            f"background:{colors.panel_background};color:{colors.text};"
            f"border:1px solid {colors.border};padding:5px;}}"
            "QMenu::item{padding:8px 28px 8px 12px;border-radius:4px;}"
            "QMenu::item:selected{"
            f"background:{colors.hover};color:{colors.highlight};}}"
            "QMenu::separator{height:1px;"
            f"background:{colors.border};margin:5px 8px;}}"
        )
        for menu in (self, self.light_menu, self.dark_menu):
            if menu is not None:
                menu.setStyleSheet(menu_qss)
                menu.setToolTipsVisible(True)
        for row in self._leaf_rows.values():
            row.apply_theme_tokens(colors)

    def leaf_action(self, style: str) -> QtWidgets.QWidgetAction | None:
        return self._leaf_actions.get(normalize_curtain_style(style))

    def leaf_widget(self, style: str) -> _CurtainMenuRow | None:
        return self._leaf_rows.get(normalize_curtain_style(style))

    def _label(self, style: str) -> str:
        if self._model is not None:
            row = self._model.row_for_style(style)
            if row >= 0:
                return str(self._model.index(row, 0).data(Qt.DisplayRole) or "")
        return CURTAIN_STYLE_LABELS.get(style, style)

    def _add_leaf(self, menu: QtWidgets.QMenu, style: str) -> None:
        if self._model is None or self._model.row_for_style(style) < 0:
            return
        action = QtWidgets.QWidgetAction(menu)
        action.setText(self._label(style))
        action.setData(style)
        action.triggered.connect(
            lambda _checked=False, selected=style: self._activate(selected)
        )
        row = _CurtainMenuRow(
            style,
            self._model,
            lambda _style: action.trigger(),
            menu,
        )
        row.set_current_style(self._current_style)
        row.apply_theme_tokens(self._theme_tokens)
        action.setDefaultWidget(row)
        menu.addAction(action)
        self._leaf_actions[style] = action
        self._leaf_rows[style] = row

    @QtCore.Slot()
    def _rebuild(self, *_args: object) -> None:
        self.clear()
        self._leaf_actions.clear()
        self._leaf_rows.clear()
        self.light_menu = None
        self.dark_menu = None
        if self._model is None:
            return
        for style in _DIRECT_STYLES:
            self._add_leaf(self, style)
        self.addSeparator()
        self.light_menu = QtWidgets.QMenu("Light Curtains", self)
        self.light_menu.setObjectName(f"{self.objectName()}LightMenu")
        self.addMenu(self.light_menu)
        for style in _LIGHT_STYLES:
            self._add_leaf(self.light_menu, style)
        self.dark_menu = QtWidgets.QMenu("Dark Curtains", self)
        self.dark_menu.setObjectName(f"{self.objectName()}DarkMenu")
        self.addMenu(self.dark_menu)
        for style in _DARK_STYLES:
            self._add_leaf(self.dark_menu, style)
        self.apply_theme_tokens(self._theme_tokens)

    @QtCore.Slot()
    def _before_show(self) -> None:
        self.previewColorsRequested.emit()
        self._refresh_rows()

    @QtCore.Slot()
    def _refresh_rows(self, *_args: object) -> None:
        for row in self._leaf_rows.values():
            row.set_current_style(self._current_style)

    def _activate(self, style: str) -> None:
        self.styleActivated.emit(normalize_curtain_style(style))
        self.close()


class CurtainStyleComboBox(QtWidgets.QToolButton):
    """A themed closed value with a hierarchical curtain-color popup."""

    styleActivated = QtCore.Signal(str)
    previewColorsRequested = QtCore.Signal()
    activated = QtCore.Signal(int)

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        object_name: str = "CurtainStyleSelector",
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(250, 38)
        self.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._model: CurtainStyleModel | None = None
        self._current_style = DEFAULT_CURTAIN_STYLE
        self._choices_available = True
        self._theme_tokens = CYBER_FORGE_THEME.tokens
        self._popup_anchors: list[QtWidgets.QWidget] = []
        self._choice_menu = _CurtainChoiceMenu(
            "",
            self,
            object_name=f"{object_name}Popup",
        )
        self.setMenu(self._choice_menu)
        self._choice_menu.installEventFilter(self)
        self._choice_menu.aboutToShow.connect(self._track_popup_anchor)
        self._choice_menu.aboutToHide.connect(self._release_popup_anchor)
        self._choice_menu.styleActivated.connect(self._activate_style)
        self._choice_menu.previewColorsRequested.connect(
            self.previewColorsRequested
        )
        self.activated.connect(self._activated_index)
        self._arrow = QtWidgets.QLabel("\N{DOWN ARROWHEAD}", self)
        self._arrow.setAlignment(Qt.AlignCenter)
        self._arrow.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.apply_theme_tokens(CYBER_FORGE_THEME.tokens)

    def setModel(self, model: CurtainStyleModel) -> None:
        if model is self._model:
            return
        if self._model is not None:
            try:
                self._model.dataChanged.disconnect(self._model_changed)
            except (RuntimeError, TypeError):
                pass
        self._model = model
        model.dataChanged.connect(self._model_changed)
        self._choice_menu.set_model(model)
        self.set_current_style(self._current_style)

    def model(self) -> CurtainStyleModel | None:
        return self._model

    def count(self) -> int:
        if self._model is None or not self._choices_available:
            return 0
        return self._model.rowCount()

    def set_choices_available(self, available: bool) -> None:
        available = bool(available)
        if self._choices_available == available:
            return
        self._choices_available = available
        if available:
            self.set_current_style(self._current_style)
        else:
            self.hidePopup()
            self.setText("")
        self._arrow.setVisible(available)
        self._refresh_selected_visual()

    def itemData(self, index: int, role: int = CURTAIN_STYLE_ROLE) -> object:
        if (
            not self._choices_available
            or self._model is None
            or not 0 <= index < self._model.rowCount()
        ):
            return None
        return self._model.index(index, 0).data(role)

    def itemText(self, index: int) -> str:
        return str(self.itemData(index, Qt.DisplayRole) or "")

    def findData(self, value: object, role: int = CURTAIN_STYLE_ROLE) -> int:
        for index in range(self.count()):
            if self.itemData(index, role) == value:
                return index
        return -1

    def currentIndex(self) -> int:
        return self.findData(self._current_style, CURTAIN_STYLE_ROLE)

    def setCurrentIndex(self, index: int) -> None:
        style = self.itemData(index, CURTAIN_STYLE_ROLE)
        if style is not None:
            self.set_current_style(str(style))

    def currentData(self, role: int = CURTAIN_STYLE_ROLE) -> object:
        return self.itemData(self.currentIndex(), role)

    def currentText(self) -> str:
        return self.text()

    @QtCore.Slot(str)
    def set_current_style(self, style: str) -> None:
        normalized = normalize_curtain_style(style)
        if self._model is not None and self._model.row_for_style(normalized) < 0:
            normalized = DEFAULT_CURTAIN_STYLE
        self._current_style = normalized
        self._choice_menu.set_current_style(normalized)
        label = CURTAIN_STYLE_LABELS.get(normalized, normalized)
        if self._model is not None:
            row = self._model.row_for_style(normalized)
            if row >= 0:
                label = str(
                    self._model.index(row, 0).data(Qt.DisplayRole) or label
                )
        self.setText(label if self._choices_available else "")
        self._refresh_selected_visual()

    def current_style(self) -> str:
        return self._current_style

    def current_display_colors(self) -> CurtainDisplayColors | None:
        if self._model is None:
            return None
        return self._model.display_colors(self._current_style)

    def apply_theme_assets(self, service: object) -> None:
        self.apply_theme_tokens(service.tokens)

    def apply_theme_tokens(self, colors: ThemeTokens) -> None:
        self._theme_tokens = colors
        self.hidePopup()
        self._choice_menu.apply_theme_tokens(colors)
        self._arrow.setStyleSheet(
            f"color:{colors.accent};background:transparent;"
            "font:700 13pt 'Segoe UI Symbol';"
        )
        self._refresh_selected_visual()

    def showPopup(self) -> None:
        if self._choices_available and self.isEnabled():
            self.showMenu()

    def hidePopup(self) -> None:
        for menu in (self._choice_menu.light_menu, self._choice_menu.dark_menu):
            if menu is not None:
                menu.close()
        self._choice_menu.close()

    def popup_menu(self) -> QtWidgets.QMenu:
        return self._choice_menu

    @QtCore.Slot(str)
    def _activate_style(self, style: str) -> None:
        index = self.findData(normalize_curtain_style(style), CURTAIN_STYLE_ROLE)
        if index >= 0:
            self.activated.emit(index)

    @QtCore.Slot(int)
    def _activated_index(self, index: int) -> None:
        style = self.itemData(index, CURTAIN_STYLE_ROLE)
        if style is None:
            return
        normalized = normalize_curtain_style(style)
        self.set_current_style(normalized)
        self.styleActivated.emit(normalized)

    @QtCore.Slot()
    def _model_changed(self, *_args: object) -> None:
        self._refresh_selected_visual()

    def _refresh_selected_visual(self) -> None:
        colors = self._theme_tokens
        background = _qcolor(colors.control_background, "#202830")
        foreground = _qcolor(colors.text, "#ffffff")
        if self._choices_available and self._model is not None:
            row = self._model.row_for_style(self._current_style)
            if row >= 0:
                index = self._model.index(row, 0)
                background = _qcolor(
                    index.data(CURTAIN_BACKGROUND_ROLE),
                    background.name(),
                )
                foreground = _qcolor(
                    index.data(CURTAIN_FOREGROUND_ROLE),
                    foreground.name(),
                )
        self.setStyleSheet(
            f"QToolButton#{self.objectName()}{{"
            f"background:{background.name()};color:{foreground.name()};"
            f"border:1px solid {colors.accent};border-radius:8px;"
            "padding:4px 38px 4px 10px;font:600 10pt 'Segoe UI';"
            "text-align:left;}"
            f"QToolButton#{self.objectName()}:hover{{"
            f"background:{background.name()};color:{foreground.name()};"
            f"border:2px solid {colors.highlight};"
            "padding:3px 37px 3px 9px;}"
            f"QToolButton#{self.objectName()}:focus{{"
            f"background:{background.name()};color:{foreground.name()};"
            f"border:2px solid {colors.accent};"
            "padding:3px 37px 3px 9px;}"
            f"QToolButton#{self.objectName()}:disabled{{"
            f"background:{colors.control_background};color:{colors.muted_text};"
            f"border:1px solid {colors.border};}}"
            f"QToolButton#{self.objectName()}::menu-indicator{{image:none;}}"
        )
        self.update()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        self._sync_popup_width()
        self._arrow.setGeometry(self.width() - 33, 1, 31, self.height() - 2)
        self._arrow.raise_()
        super().resizeEvent(event)

    def _sync_popup_width(self) -> None:
        width = self.width()
        self._choice_menu.setFixedWidth(width)
        for style in _DIRECT_STYLES:
            row = self._choice_menu.leaf_widget(style)
            if row is not None:
                row.setFixedWidth(width - 12)

    def _track_popup_anchor(self) -> None:
        self._sync_popup_width()
        self._release_popup_anchor()
        anchor: QtWidgets.QWidget | None = self
        while anchor is not None:
            anchor.installEventFilter(self)
            self._popup_anchors.append(anchor)
            anchor = anchor.parentWidget()

    def _release_popup_anchor(self) -> None:
        for anchor in self._popup_anchors:
            try:
                anchor.removeEventFilter(self)
            except RuntimeError:
                pass
        self._popup_anchors.clear()

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if not hasattr(self, "_choice_menu"):
            return super().eventFilter(watched, event)
        if watched is self._choice_menu and event.type() == QtCore.QEvent.Show:
            # QMenu leaves a one-pixel screen-edge inset on Windows. When the
            # field fits, keep its border alignment while retaining Qt's y placement.
            field = QtCore.QRect(self.mapToGlobal(QtCore.QPoint()), self.size())
            available = self.screen().availableGeometry()
            if available.left() <= field.left() and field.right() <= available.right():
                self._choice_menu.move(field.left(), self._choice_menu.y())
        if watched in self._popup_anchors and event.type() in (
            QtCore.QEvent.Move,
            QtCore.QEvent.Resize,
            QtCore.QEvent.Hide,
            QtCore.QEvent.ParentChange,
            QtCore.QEvent.WindowStateChange,
            QtCore.QEvent.DevicePixelRatioChange,
        ):
            self.hidePopup()
        return super().eventFilter(watched, event)


class CurtainStyleMenuSelector(QtCore.QObject):
    """Settings-menu access point using the same selector contract."""

    styleActivated = QtCore.Signal(str)
    previewColorsRequested = QtCore.Signal()
    activated = QtCore.Signal(int)

    def __init__(
        self,
        title: str,
        parent: QtWidgets.QWidget,
        *,
        object_name: str = "CurtainStyleMenuSelector",
    ) -> None:
        super().__init__(parent)
        self._model: CurtainStyleModel | None = None
        self._current_style = DEFAULT_CURTAIN_STYLE
        self._choice_menu = _CurtainChoiceMenu(
            title,
            parent,
            object_name=object_name,
        )
        self._choice_menu.styleActivated.connect(self._activate_style)
        self._choice_menu.previewColorsRequested.connect(
            self.previewColorsRequested
        )
        self.activated.connect(self._activated_index)

    def menu(self) -> QtWidgets.QMenu:
        return self._choice_menu

    def setModel(self, model: CurtainStyleModel) -> None:
        self._model = model
        self._choice_menu.set_model(model)
        self.set_current_style(self._current_style)

    def model(self) -> CurtainStyleModel | None:
        return self._model

    def count(self) -> int:
        return 0 if self._model is None else self._model.rowCount()

    def itemData(self, index: int, role: int = CURTAIN_STYLE_ROLE) -> object:
        if self._model is None or not 0 <= index < self._model.rowCount():
            return None
        return self._model.index(index, 0).data(role)

    def itemText(self, index: int) -> str:
        return str(self.itemData(index, Qt.DisplayRole) or "")

    def findData(self, value: object, role: int = CURTAIN_STYLE_ROLE) -> int:
        for index in range(self.count()):
            if self.itemData(index, role) == value:
                return index
        return -1

    def currentIndex(self) -> int:
        return self.findData(self._current_style, CURTAIN_STYLE_ROLE)

    def setCurrentIndex(self, index: int) -> None:
        style = self.itemData(index, CURTAIN_STYLE_ROLE)
        if style is not None:
            self.set_current_style(str(style))

    def currentData(self, role: int = CURTAIN_STYLE_ROLE) -> object:
        return self.itemData(self.currentIndex(), role)

    @QtCore.Slot(str)
    def set_current_style(self, style: str) -> None:
        normalized = normalize_curtain_style(style)
        if self._model is not None and self._model.row_for_style(normalized) < 0:
            normalized = DEFAULT_CURTAIN_STYLE
        self._current_style = normalized
        self._choice_menu.set_current_style(normalized)

    def current_style(self) -> str:
        return self._current_style

    def current_display_colors(self) -> CurtainDisplayColors | None:
        if self._model is None:
            return None
        return self._model.display_colors(self._current_style)

    def apply_theme_assets(self, service: object) -> None:
        self.apply_theme_tokens(service.tokens)

    def apply_theme_tokens(self, colors: ThemeTokens) -> None:
        self._choice_menu.apply_theme_tokens(colors)

    @QtCore.Slot(str)
    def _activate_style(self, style: str) -> None:
        index = self.findData(normalize_curtain_style(style), CURTAIN_STYLE_ROLE)
        if index >= 0:
            self.activated.emit(index)

    @QtCore.Slot(int)
    def _activated_index(self, index: int) -> None:
        style = self.itemData(index, CURTAIN_STYLE_ROLE)
        if style is None:
            return
        normalized = normalize_curtain_style(style)
        self.set_current_style(normalized)
        self.styleActivated.emit(normalized)


CurtainSelector = CurtainStyleComboBox | CurtainStyleMenuSelector


class CurtainStyleController(QtCore.QObject):
    """One settings-backed selection and color model for all access points."""

    styleChanged = QtCore.Signal(str)
    styleCommitted = QtCore.Signal(str)
    previewColorsRequested = QtCore.Signal()
    _settingsStyleReceived = QtCore.Signal(str)

    def __init__(
        self,
        settings: SettingsStore,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.model = CurtainStyleModel(self)
        self._selectors: list[CurtainSelector] = []
        self.current_style = normalize_curtain_style(
            settings.get("curtain_style", DEFAULT_CURTAIN_STYLE)
        )
        self._closed = False
        self._settingsStyleReceived.connect(
            self._apply_style,
            Qt.ConnectionType.QueuedConnection,
        )
        self.settings.changed.connect(self._on_settings_changed)

    def bind(self, selector: CurtainSelector) -> None:
        if self._closed:
            raise RuntimeError("Closed curtain controller cannot bind selectors.")
        if selector in self._selectors:
            selector.set_current_style(self.current_style)
            return
        selector.setModel(self.model)
        selector.styleActivated.connect(self.set_style)
        selector.previewColorsRequested.connect(self._request_preview_colors)
        self.styleChanged.connect(selector.set_current_style)
        self._selectors.append(selector)
        selector.set_current_style(self.current_style)

    @QtCore.Slot(str)
    def set_style(self, style: str) -> None:
        if self._closed:
            return
        normalized = normalize_curtain_style(style)
        before = normalize_curtain_style(
            self.settings.get("curtain_style", DEFAULT_CURTAIN_STYLE)
        )
        snapshot = self.settings.update_fields(curtain_style=normalized)
        stored = normalize_curtain_style(snapshot.get("curtain_style"))
        self._apply_style(stored)
        if stored != before:
            self.styleCommitted.emit(stored)

    def sync_from_settings(
        self,
        snapshot: Mapping[str, object] | None = None,
    ) -> str:
        if self._closed:
            return self.current_style
        current = snapshot if snapshot is not None else self.settings.snapshot()
        style = normalize_curtain_style(current.get("curtain_style"))
        self._apply_style(style)
        return style

    def set_preview_colors(
        self,
        colors: Mapping[str, tuple[int, int, int]],
    ) -> None:
        if not self._closed:
            self.model.set_preview_colors(colors)

    @QtCore.Slot()
    def _request_preview_colors(self) -> None:
        if not self._closed:
            self.previewColorsRequested.emit()

    def _on_settings_changed(
        self,
        settings: dict[str, object],
        keys: tuple[str, ...],
    ) -> None:
        if not self._closed and "curtain_style" in keys:
            self._settingsStyleReceived.emit(
                normalize_curtain_style(settings.get("curtain_style"))
            )

    @QtCore.Slot(str)
    def _apply_style(self, style: str) -> None:
        if self._closed:
            return
        normalized = normalize_curtain_style(style)
        if normalized == self.current_style:
            for selector in self._selectors:
                selector.set_current_style(normalized)
            return
        self.current_style = normalized
        self.styleChanged.emit(normalized)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.settings.changed.disconnect(self._on_settings_changed)
        for selector in self._selectors:
            disconnectors = (
                lambda: selector.styleActivated.disconnect(self.set_style),
                lambda: selector.previewColorsRequested.disconnect(
                    self._request_preview_colors
                ),
                lambda: self.styleChanged.disconnect(selector.set_current_style),
            )
            for disconnect in disconnectors:
                try:
                    disconnect()
                except (RuntimeError, TypeError):
                    pass
        self._selectors.clear()


__all__ = [
    "CURTAIN_BACKGROUND_ROLE",
    "CURTAIN_FOREGROUND_ROLE",
    "CURTAIN_STYLE_ROLE",
    "CurtainStyleComboBox",
    "CurtainStyleController",
    "CurtainStyleDelegate",
    "CurtainStyleMenuSelector",
    "CurtainStyleModel",
]
