"""A horizontal artwork strip backed by the canonical Images session."""
import re

from PySide6 import QtCore, QtGui, QtWidgets

from images_workspace import ImageTab as _ImagesWorkspace, PageNavigator
from project_model import PAGE_TYPES, MAX_PAGES
from project_paths import application_paths
from prompt_writer_project import page_label


def _artwork_rect(cell):
    return cell.adjusted(36, 94, -36, -40)


def _title_rect(cell):
    return QtCore.QRect(cell.x()+30, cell.y()+57, cell.width()-60, 28)


class _ArtworkStrip(PageNavigator):
    import_requested = QtCore.Signal(object)
    choose_requested = QtCore.Signal(object)
    rename_requested = QtCore.Signal(object)
    geometry_changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QtWidgets.QListView.IconMode)
        self.setFlow(QtWidgets.QListView.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QtWidgets.QListView.Snap)
        self.setResizeMode(QtWidgets.QListView.Adjust)
        self.setSpacing(0)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setDropIndicatorShown(False)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setStyleSheet("QListWidget { border: none; background: transparent; }")
        self.viewport().setCursor(QtCore.Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._wheel_remainder = 0.0
        self._press_position = None
        self._double_click = False
        self._pending_choice = None
        self._choice_timer = QtCore.QTimer(self)
        self._choice_timer.setSingleShot(True)
        self._choice_timer.timeout.connect(self._choose_after_click)
        self._drag_session = None
        self._drag_point = None
        self._drag_row = -1
        self._drag_scroll = QtCore.QTimer(self)
        self._drag_scroll.setInterval(35)
        self._drag_scroll.timeout.connect(self._scroll_while_dragging)

    def mousePressEvent(self, event):
        self._choice_timer.stop()
        self._press_position = event.position().toPoint()
        item = self.itemAt(self._press_position)
        self._press_target = (self.property("projectSession"), item.data(QtCore.Qt.UserRole)) if item else None
        self._double_click = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        point = event.position().toPoint()
        item = self.itemAt(point)
        if (event.button() == QtCore.Qt.LeftButton and not self._double_click
                and self._press_position is not None and item is not None
                and self._press_target is not None and self._press_target[1] == item.data(QtCore.Qt.UserRole)
                and (point - self._press_position).manhattanLength() < QtWidgets.QApplication.startDragDistance()
                and _artwork_rect(self.visualItemRect(item)).contains(point)):
            self._pending_choice = self._press_target
            # Keep the existing double-click file import as a separate gesture.
            self._choice_timer.start(QtWidgets.QApplication.doubleClickInterval())
        self._press_position = None

    def _choose_after_click(self):
        if self._pending_choice is None:
            return
        session, identity = self._pending_choice
        self._pending_choice = None
        if self.isVisible() and session == self.property("projectSession"):
            self.choose_requested.emit(identity)

    def wheelEvent(self, event):
        bar = self.horizontalScrollBar()
        pixel, angle = event.pixelDelta(), event.angleDelta()
        if not pixel.isNull():
            delta = pixel.x() if abs(pixel.x()) > abs(pixel.y()) else pixel.y()
        else:
            delta = (angle.x() if abs(angle.x()) > abs(angle.y()) else angle.y()) * 0.8
        self._wheel_remainder -= delta
        steps = int(self._wheel_remainder)
        self._wheel_remainder -= steps
        if steps and bar.maximum() > 0:
            bar.setValue(bar.value() + steps)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self._choice_timer.stop()
        self._double_click = True
        point = event.position().toPoint()
        item = self.itemAt(point)
        if event.button() == QtCore.Qt.LeftButton and item is not None:
            rect = self.visualItemRect(item)
            for area, action in ((_title_rect(rect), self.rename_requested),
                                 (_artwork_rect(rect), self.import_requested)):
                if area.contains(point):
                    action.emit(item.data(QtCore.Qt.UserRole))
                    event.accept()
                    return
        super().mouseDoubleClickEvent(event)

    def startDrag(self, _actions):
        item = self.currentItem()
        if item is None:
            return
        mime = QtCore.QMimeData()
        mime.setData(self._PAGE_MIME, item.data(QtCore.Qt.UserRole).encode("utf-8"))
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(item.icon().pixmap(80, 100))
        self._drag_session = self.property("projectSession")
        try:
            drag.exec(QtCore.Qt.MoveAction)
        finally:
            self._drag_session = None
            self._clear_drop_marker()
            drag.deleteLater()

    def _source_row(self, event):
        if (event.source() is not self or not event.mimeData().hasFormat(self._PAGE_MIME)
                or self._drag_session not in (None, self.property("projectSession"))):
            return -1
        identity = bytes(event.mimeData().data(self._PAGE_MIME)).decode("utf-8")
        return next((row for row in range(self.count()) if self.item(row).data(QtCore.Qt.UserRole) == identity), -1)

    def _insertion_gap(self, point, source):
        for row in range(self.count()):
            midpoint = self.visualItemRect(self.item(row)).center().x()
            if point.x() < midpoint or (point.x() == midpoint and source > row):
                return row
        return self.count()

    def dragEnterEvent(self, event):
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        source = self._source_row(event)
        if source < 0:
            self._clear_drop_marker()
            event.ignore()
            return
        self._drag_point = event.position().toPoint()
        self._drag_row = source
        if not self._drag_scroll.isActive():
            self._drag_scroll.start()
        self.viewport().update()
        event.setDropAction(QtCore.Qt.MoveAction)
        event.accept()

    def dragLeaveEvent(self, event):
        self._clear_drop_marker()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        source = self._source_row(event)
        self._clear_drop_marker()
        if source < 0:
            event.ignore()
            return
        gap = self._insertion_gap(event.position().toPoint(), source)
        destination = gap - (gap > source)
        if source != destination:
            ids = [self.item(row).data(QtCore.Qt.UserRole) for row in range(self.count())]
            ids.insert(destination, ids.pop(source))
            self.order_changed.emit(ids)
        event.setDropAction(QtCore.Qt.MoveAction)
        event.accept()

    def _scroll_while_dragging(self):
        if self._drag_point is None:
            return
        bar = self.horizontalScrollBar()
        edge = self._drag_point.x()
        if edge < 32:
            bar.setValue(bar.value() - 24)
        elif edge >= self.viewport().width() - 32:
            bar.setValue(bar.value() + 24)

    def _clear_drop_marker(self):
        self._drag_point = None
        self._drag_row = -1
        self._drag_scroll.stop()
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drag_point is None or not 0 <= self._drag_row < self.count():
            return
        gap = self._insertion_gap(self._drag_point, self._drag_row)
        if gap in (self._drag_row, self._drag_row + 1):
            return
        end = gap == self.count()
        cell = self.visualItemRect(self.item(gap - 1 if end else gap))
        x = cell.right() if end else cell.left()
        x = max(3, min(x, self.viewport().width() - 4))
        painter = QtGui.QPainter(self.viewport())
        painter.setPen(QtGui.QPen(self.palette().highlight(), 4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        painter.drawLine(x, cell.top()+10, x, cell.bottom()-10)
        painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.geometry_changed.emit()


class _ArtworkDelegate(QtWidgets.QStyledItemDelegate):
    _type_colors = {"cover": "#c59b54", "illustration": "#68a8b5",
                    "letter": "#a599c8", "end_page": "#91ad8d"}

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        cell, palette = option.rect, option.palette
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        hovered = bool(option.state & QtWidgets.QStyle.State_MouseOver)
        focused = bool(option.state & QtWidgets.QStyle.State_HasFocus)
        foreground = palette.text().color()
        muted = QtGui.QColor(foreground)
        muted.setAlpha(170)
        frame = QtCore.QRectF(cell.adjusted(10, 6, -10, -6))
        accent = QtGui.QColor(self._type_colors.get(index.data(QtCore.Qt.UserRole+4), "#c59b54"))
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(0, 0, 0, 18))
        painter.drawRoundedRect(frame.translated(0, 3), 12, 12)
        painter.setBrush(palette.base())
        border = QtGui.QColor(accent if selected or hovered else palette.mid().color())
        border.setAlpha(230 if selected else 150 if hovered else 75)
        painter.setPen(QtGui.QPen(border, 2 if selected else 1))
        painter.drawRoundedRect(frame, 12, 12)
        tint = QtGui.QColor(accent)
        tint.setAlpha(12 if selected else 5)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(tint)
        painter.drawRoundedRect(frame, 12, 12)
        if focused:
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.setPen(QtGui.QPen(palette.highlight(), 1, QtCore.Qt.DotLine))
            painter.drawRoundedRect(frame.adjusted(4, 4, -4, -4), 9, 9)
        font = QtGui.QFont(option.font)
        font.setPointSizeF(max(9, font.pointSizeF()))
        font.setWeight(QtGui.QFont.DemiBold)
        painter.setFont(font)
        badge = QtCore.QRect(cell.x()+30, cell.y()+21, 30, 26)
        painter.setPen(muted)
        painter.drawText(badge, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, f"{index.row()+1:02}")
        kind = index.data(QtCore.Qt.UserRole+3) or ""
        right_inset = 68 if selected else 30
        category_width = min(cell.width()-70-right_inset, painter.fontMetrics().horizontalAdvance(kind)+22)
        category = QtCore.QRect(cell.right()-right_inset-category_width, badge.top(), category_width, 26)
        tint.setAlpha(28)
        painter.setBrush(tint)
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawRoundedRect(category, 8, 8)
        painter.setPen(foreground)
        painter.drawText(category, QtCore.Qt.AlignCenter,
                         painter.fontMetrics().elidedText(kind, QtCore.Qt.ElideRight, category.width()-8))
        title_rect = _title_rect(cell)
        font.setPointSizeF(max(11, font.pointSizeF()+1))
        painter.setFont(font)
        painter.drawText(title_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                         painter.fontMetrics().elidedText(index.data(QtCore.Qt.UserRole+5) or kind,
                                                         QtCore.Qt.ElideRight, title_rect.width()))
        art = _artwork_rect(cell)
        icon = index.data(QtCore.Qt.DecorationRole)
        if icon is not None and not icon.isNull():
            image_rect = QtCore.QRect(QtCore.QPoint(), icon.actualSize(art.adjusted(3, 3, -3, -3).size()))
            image_rect.moveCenter(art.center())
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(0, 0, 0, 35))
            painter.drawRoundedRect(image_rect.adjusted(-3, 1, 3, 6), 3, 3)
            icon.paint(painter, image_rect, QtCore.Qt.AlignCenter)
        else:
            painter.setBrush(palette.window())
            border.setAlpha(90)
            painter.setPen(QtGui.QPen(border, 1, QtCore.Qt.DashLine))
            painter.drawRoundedRect(art, 8, 8)
            if not (selected and index.data(QtCore.Qt.UserRole+6)):
                center = art.center() - QtCore.QPoint(0, 12)
                painter.setPen(QtGui.QPen(muted, 2))
                painter.drawLine(center + QtCore.QPoint(-8, 0), center + QtCore.QPoint(8, 0))
                painter.drawLine(center + QtCore.QPoint(0, -8), center + QtCore.QPoint(0, 8))
                painter.setPen(muted)
                painter.drawText(QtCore.QRect(art.left()+8, center.y()+12, art.width()-16, 24),
                                 QtCore.Qt.AlignCenter, "Choose image")
        font.setPointSizeF(max(9, option.font.pointSizeF()))
        font.setWeight(QtGui.QFont.Normal)
        painter.setFont(font)
        status = index.data(QtCore.Qt.UserRole+2) or ""
        if status in ("Ready", "Image missing"):
            status = "Selected" if selected else "Choose image" if hovered else ""
        painter.setPen(muted)
        status_width = cell.width()-60
        painter.drawText(QtCore.QRect(cell.x()+30, cell.bottom()-33, status_width, 22),
                         QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                         painter.fontMetrics().elidedText(status, QtCore.Qt.ElideRight, status_width))
        painter.restore()


class ImageTab(_ImagesWorkspace):
    navigator_class = _ArtworkStrip

    def __init__(self, *args, **kwargs):
        self._grid_ready = False
        self._last_strip_selection = None
        self._turn_dialog = None
        super().__init__(*args, **kwargs)
        root = self.layout()
        while root.count():
            item = root.takeAt(0)
            if item.widget():
                item.widget().hide()
        self._header_layout = QtWidgets.QGridLayout()
        self._header_layout.setHorizontalSpacing(0)
        self._header_layout.setColumnStretch(0, 1)
        self._header_layout.setColumnStretch(2, 1)
        self._header_layout.addWidget(self.pwrite_fab, 0, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.pwrite_fab.set_surface(self)
        self._identity_panel = QtWidgets.QFrame()
        self._identity_panel.setObjectName("ImagesPageDetails")
        self._identity_panel.setProperty("themeRole", "panel")
        self._identity_panel.setStyleSheet(
            "QFrame#ImagesPageDetails { border-radius: 12px; }"
            "QLineEdit, QComboBox { min-height: 28px; padding: 2px 8px; border-radius: 6px; }"
            "QPushButton, QToolButton { min-height: 26px; padding: 2px 12px; border-radius: 6px; }")
        identity_panel_layout = QtWidgets.QVBoxLayout(self._identity_panel)
        identity_panel_layout.setContentsMargins(16, 12, 16, 12)
        identity_panel_layout.setSpacing(10)
        self._selection_label = QtWidgets.QLabel()
        self._selection_label.setStyleSheet("font-weight: 600;")
        identity_panel_layout.addWidget(self._selection_label, 0, QtCore.Qt.AlignLeft)
        self._identity = QtWidgets.QBoxLayout(QtWidgets.QBoxLayout.LeftToRight)
        self._identity.setSpacing(12)
        self._field_labels = []
        for label, control in (("Page name", self.name_edit), ("Page type", self.type_combo),
                               ("Letter transition", self.transition_combo)):
            column = QtWidgets.QHBoxLayout()
            column.setSpacing(8)
            column.setAlignment(QtCore.Qt.AlignVCenter)
            field_label = QtWidgets.QLabel(label)
            field_label.setBuddy(control)
            field_label.setStyleSheet("font-weight: bold;")
            control.setStyleSheet("font-weight: bold;")
            self._field_labels.append(field_label)
            column.addWidget(field_label, 0, QtCore.Qt.AlignVCenter)
            column.addWidget(control, 0, QtCore.Qt.AlignVCenter)
            self._identity.addLayout(column)
        self.name_edit.setMinimumWidth(0)
        self.type_combo.setFixedWidth(130)
        self.transition_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        identity_panel_layout.addLayout(self._identity)
        self.type_note = QtWidgets.QLabel()
        self.type_note.setAlignment(QtCore.Qt.AlignCenter)
        self.type_note.hide()
        identity_panel_layout.addWidget(self.type_note)
        actions = QtWidgets.QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.add_button)
        self.preview_turn_button = self._button("Preview Turn", self._preview_turn)
        actions.addWidget(self.preview_turn_button)
        actions.addStretch()
        identity_panel_layout.addLayout(actions)
        self._header_layout.addWidget(self._identity_panel, 0, 1, QtCore.Qt.AlignCenter)
        root.addLayout(self._header_layout)
        self.page_list.setItemDelegate(_ArtworkDelegate(self.page_list))
        self.page_list.setAccessibleName("Pages in order from left to right")
        self.page_list.setAccessibleDescription(
            "Click artwork to choose an image. Double-click the title or press F2 to rename. "
            "Scroll to browse. Use Page actions or right-click for the menu. "
            "Control plus Left or Right moves the selected page.")
        self.page_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.page_list.customContextMenuRequested.connect(self._show_page_menu)
        self.page_list.import_requested.connect(self._import_page)
        self.page_list.choose_requested.connect(self._choose_page)
        self.page_list.rename_requested.connect(self._rename_from_card)
        root.addWidget(self.page_list, 1)
        self._deletion_notice = QtWidgets.QWidget()
        notice = QtWidgets.QHBoxLayout(self._deletion_notice)
        notice.setContentsMargins(0, 0, 0, 0)
        notice.addWidget(QtWidgets.QLabel("Page deleted."))
        self.undo_button.setText("Undo")
        self.undo_button.setAccessibleName("Undo page deletion")
        notice.addWidget(self.undo_button)
        notice.addStretch(1)
        root.addWidget(self._deletion_notice)
        root.addWidget(self.status)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_background_menu)
        for button, text, description in ((self.up_button, "‹", "Move page left"),
                                           (self.down_button, "›", "Move page right")):
            button.setParent(self.page_list.viewport())
            button.setText(text)
            button.setAccessibleName(description)
            button.setToolTip(description)
            button.setFixedSize(28, 44)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setStyleSheet(
                "QPushButton { border: none; background: transparent; font-size: 26px; }"
                "QPushButton:hover { background: palette(alternate-base); border-radius: 4px; }"
                "QPushButton:focus { border: 1px solid palette(highlight); }")
        self.page_actions_button = QtWidgets.QToolButton(self.page_list.viewport())
        self.page_actions_button.setText("⋯")
        self.page_actions_button.setAccessibleName("Page actions")
        self.page_actions_button.setToolTip("Page actions")
        self.page_actions_button.setFixedSize(30, 27)
        self.page_actions_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.page_actions_button.setStyleSheet("QToolButton { font-size: 17px; padding: 0; }")
        self.page_actions_button.clicked.connect(self._show_selected_page_menu)
        self.restore_artwork_button = self._button("Restore Artwork", self._restore_selected_artwork)
        self.restore_artwork_button.setParent(self.page_list.viewport())
        self.page_list.horizontalScrollBar().valueChanged.connect(self._position_card_controls)
        self.page_list.geometry_changed.connect(self._resize_strip)
        for key, callback in (("Return", lambda: self._choose_page(self.session.selected_page_id)),
                              ("Space", lambda: self._choose_page(self.session.selected_page_id)),
                              ("F2", lambda: self._rename_from_card(self.session.selected_page_id)),
                              ("Ctrl+O", lambda: self._import_page(self.session.selected_page_id)),
                              ("Ctrl+Left", lambda: self._run(lambda: self.move_page(self.session.selected_page_id, -1))),
                              ("Ctrl+Right", lambda: self._run(lambda: self.move_page(self.session.selected_page_id, 1)))):
            action = QtGui.QAction(self.page_list)
            action.setShortcut(QtGui.QKeySequence(key))
            action.setShortcutContext(QtCore.Qt.WidgetShortcut)
            action.triggered.connect(callback)
            self.page_list.addAction(action)
        self._grid_ready = True
        for widget in (self.page_list, self.name_edit, self.type_combo, self.transition_combo, self.pwrite_fab,
                       self.add_button, self.status):
            widget.show()
        self.refresh_cards()
        self._update_workspace_layout()

    def _select_for_action(self, identity):
        if identity is None or not any(page["id"] == identity for page in self.session.project.pages):
            return False
        for row in range(self.page_list.count()):
            if self.page_list.item(row).data(QtCore.Qt.UserRole) == identity:
                self.page_list.setCurrentRow(row)
                return self.session.selected_page_id == identity
        return False

    def _import_page(self, identity):
        if self._select_for_action(identity):
            self._run(self._browse_selected)

    def _choose_page(self, identity):
        if not self._shutdown and self.isVisible() and self._select_for_action(identity):
            self._run(self._choose_images)

    def _rename_from_card(self, identity):
        if self._select_for_action(identity):
            self._run(lambda: self._rename_selected_page(identity))

    def _rename_selected_page(self, identity):
        from ui_dialogs import LetterSmithInputDialog

        self._prepare_page_operation()
        target = self.session.capture_target(identity, fields=("name",), include_shared=False)
        page = self.session.project.get_page(identity)
        name, accepted = LetterSmithInputDialog.get_text(self, "Rename Page", "Page name", text=page["name"])
        if accepted and name != page["name"]:
            if self.session.apply_result(target, lambda project, page_id: project.rename_page(page_id, name)):
                self._commit("page_name")
            else:
                self.status.setText("The original page changed while renaming.")

    def _restore_selected_artwork(self):
        identity = self.session.selected_page_id
        if identity is not None:
            self.restore_page_image(identity)

    def _show_prompt_history(self, identity):
        self._prepare_page_operation()
        project = self.session.project
        versions = project.prompt_history(identity)
        if not versions:
            return
        target = self.session.capture_target(identity, fields=("prompt", "recovery"), include_shared=False)
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"Prompt History · {page_label(project, identity)}")
        dialog.resize(580, 380)
        layout = QtWidgets.QVBoxLayout(dialog)
        selector = QtWidgets.QComboBox()
        selector.setAccessibleName("Earlier artwork prompts")
        for text in versions:
            summary = " ".join(text.split())
            selector.addItem(summary[:68] + ("…" if len(summary) > 68 else ""))
        layout.addWidget(selector)
        wording = QtWidgets.QPlainTextEdit(versions[0])
        wording.setAccessibleName("Earlier prompt wording")
        wording.setReadOnly(True)
        selector.currentIndexChanged.connect(lambda index: wording.setPlainText(versions[index]))
        layout.addWidget(wording, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        buttons.addButton("Restore Prompt", QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        try:
            if dialog.exec() == QtWidgets.QDialog.Accepted:
                text = versions[selector.currentIndex()]
                if self.session.apply_result(target, lambda model, page_id: model.restore_prompt_version(page_id, text)):
                    self._commit("prompt_restored")
                else:
                    self.status.setText("The original prompt changed while its history was open.")
        finally:
            dialog.deleteLater()

    def _page_menu(self, identity):
        menu = QtWidgets.QMenu(self)
        session_id = self.session.session_id

        def invoke(callback, needs_page=True):
            if self.session.session_id != session_id:
                return
            if needs_page and not self._select_for_action(identity):
                return
            self._run(callback)

        def add(text, callback, enabled=True):
            action = menu.addAction(text)
            action.setEnabled(enabled)
            action.triggered.connect(lambda checked=False: invoke(callback))
            return action

        if identity is not None:
            page = self.session.project.get_page(identity)
            add("Rename page…", lambda: self._rename_selected_page(identity))
            add("Duplicate page", lambda: self.duplicate_page(identity), self.duplicate_button.isEnabled())
            add("Delete page…", lambda: self.delete_page(identity), self.delete_button.isEnabled())
            menu.addSeparator()
            add("Choose Artwork…", self._choose_images)
            cached = self._thumbnail_cache.get(identity)
            if cached and cached[1]:
                add("Animation settings…", self._animation_selected)
            if not page["artwork"].get("asset") and page["recovery"].get("cleared_artwork"):
                add("Restore Artwork", lambda: self.restore_page_image(identity))
            else:
                add("Clear page artwork", lambda: self.clear_page_image(identity), bool(page["artwork"].get("asset")))
            menu.addSeparator()
            add("Prompt Writer", self.request_prompt)
            add("Prompt History…", lambda: self._show_prompt_history(identity), bool(self.session.project.prompt_history(identity)))
            if page["type"] == "letter":
                add("Edit letter text", self.request_message)
            menu.addSeparator()
            row = next(i for i, item in enumerate(self.session.project.pages) if item["id"] == identity)
            add("Move left", lambda: self.move_page(identity, -1), row > 0)
            add("Move right", lambda: self.move_page(identity, 1), row < len(self.session.project.pages)-1)
            menu.addSeparator()
        new_page = menu.addMenu("Add page")
        for kind, definition in PAGE_TYPES.items():
            action = new_page.addAction(definition.label)
            action.triggered.connect(lambda checked=False, kind=kind: invoke(lambda: self.add_page(kind), identity is not None))
        if self._undo:
            action = menu.addAction("Undo delete")
            action.setEnabled(self.undo_button.isEnabled())
            action.triggered.connect(lambda checked=False: invoke(self.undo_delete, False))
        return menu

    def _show_selected_page_menu(self):
        identity = self.session.selected_page_id
        if identity is not None:
            button = self.page_actions_button
            self._open_page_menu(identity, button.mapToGlobal(button.rect().bottomLeft()))

    def _open_page_menu(self, identity, position):
        menu = self._page_menu(identity)
        menu.exec(position)
        menu.deleteLater()

    def _show_page_menu(self, position):
        item = self.page_list.itemAt(position)
        identity = item.data(QtCore.Qt.UserRole) if item else None
        if identity is not None and not self._select_for_action(identity):
            return
        self._open_page_menu(identity, self.page_list.viewport().mapToGlobal(position))

    def _show_background_menu(self, position):
        self._open_page_menu(self.session.selected_page_id, self.mapToGlobal(position))

    def _preview_turn(self):
        from message_shared_preview import create_shared_preview
        from page_transitions import PAGE_TRANSITIONS

        if self._turn_dialog is not None:
            return
        self._prepare_page_operation()
        project = self.session.project
        ids = [page["id"] for page in project.pages]
        selected = self.session.selected_page_id
        if selected not in ids or len(ids) < 2:
            return
        row = min(ids.index(selected), len(ids) - 2)
        first, second = ids[row:row+2]
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Preview Turn")
        screen = self.screen().availableGeometry()
        dialog.resize(min(900, screen.width()-48), min(650, screen.height()-80))
        layout = QtWidgets.QVBoxLayout(dialog)
        heading = QtWidgets.QLabel(f"{PAGE_TRANSITIONS[project.letter_transition]} · "
                                   f"{row+1}. {page_label(project, first)} → {row+2}. {page_label(project, second)}")
        heading.setTextFormat(QtCore.Qt.PlainText)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        preview = create_shared_preview(dialog, self.session, application_paths(self.project_root).cache_root / "images" / "page-turn")
        if preview is None:
            dialog.deleteLater()
            raise RuntimeError("The letter preview renderer is unavailable.")
        preview.set_page_aspect(16, 10)
        preview.view.setAccessibleName("Page turn preview")
        layout.addWidget(preview, 1)
        status = QtWidgets.QLabel()
        status.setTextFormat(QtCore.Qt.PlainText)
        status.setWordWrap(True)
        preview.status_changed.connect(status.setText)
        layout.addWidget(status)
        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        self._turn_dialog = dialog
        try:
            preview.request(first, turn_to=second)
            dialog.exec()
        finally:
            self._turn_dialog = None
            if not preview.shutdown():
                self.status.setText("Could not release the page turn preview.")
            dialog.deleteLater()

    def _session_changed(self, event):
        if (event.kind == "project_replaced" and event.session_id == self.session.session_id
                and self._turn_dialog is not None):
            self._turn_dialog.reject()
        super()._session_changed(event)

    @staticmethod
    def _type_note(page):
        if page is None:
            return ""
        from message_format import message_plain_text

        active = page["message"]
        message = active if active is not None else page["recovery"].get("inactive_message")
        if message is None or not (message_plain_text(message["html"]).strip() or re.search(r"<img\b", message["html"], re.I)):
            return ""
        return ("Changing page type hides this message." if active is not None
                else "Message hidden. Choose Letter to show it.")

    def refresh_cards(self):
        offset = self.page_list.horizontalScrollBar().value() if self._grid_ready else 0
        selected = self.session.selected_page_id
        old_order = [self.page_list.item(row).data(QtCore.Qt.UserRole)
                     for row in range(self.page_list.count())]
        result = super().refresh_cards()
        if not self._grid_ready or not result:
            return result
        self.page_list.setProperty("projectSession", self.session.session_id)
        pages = self.session.project.pages
        selected_row = next((i for i, page in enumerate(pages)
                             if page["id"] == selected), None)
        self._selection_label.setText(f"Page {selected_row+1} of {len(pages)}"
                                      if selected_row is not None else "Select a page")
        self.preview_turn_button.setEnabled(selected_row is not None and len(pages) > 1)
        note = self._type_note(pages[selected_row] if selected_row is not None else None)
        self.type_note.setText(note)
        self.type_note.setVisible(bool(note))
        self._deletion_notice.setVisible(bool(self._undo))
        for row, page in enumerate(pages):
            item = self.page_list.item(row)
            status = item.text().split("\n")[-1]
            if status == "Message empty":
                status = "Ready"
            restorable = not page["artwork"].get("asset") and bool(page["recovery"].get("cleared_artwork", {}).get("asset"))
            if restorable:
                status = "Artwork cleared"
            item.setData(QtCore.Qt.UserRole+2, status)
            item.setData(QtCore.Qt.UserRole+3, PAGE_TYPES[page["type"]].label)
            item.setData(QtCore.Qt.UserRole+4, page["type"])
            item.setData(QtCore.Qt.UserRole+5, page.get("name") or PAGE_TYPES[page["type"]].label)
            item.setData(QtCore.Qt.UserRole+6, restorable)
            item.setText(page_label(self.session.project, page["id"]))
            item.setToolTip(f"{item.text()}\nDouble-click the name to rename · Click artwork to choose an image")
            item.setData(QtCore.Qt.AccessibleTextRole, f"{row+1}, {item.text()}, {status}")
        for widget in (self.heading, self.limit_label, self.duplicate_button,
                       self.delete_button, self.import_button, self.stock_button,
                       self.more_button, self.animation_mode, self.animation_button):
            widget.hide()
        self._size_grid()
        if (selected == self._last_strip_selection
                and old_order == [page["id"] for page in pages]):
            self.page_list.horizontalScrollBar().setValue(offset)
        elif self.page_list.currentItem() is not None:
            self.page_list.scrollToItem(self.page_list.currentItem())
        self._last_strip_selection = selected
        self._position_card_controls()
        return result

    def _size_grid(self):
        if not self._grid_ready:
            return
        available = self.page_list.contentsRect().width()
        visible = min(5, max(1, self.page_list.count()), max(1, available // 280))
        width = max(210, min(390, available // visible))
        margin = max(0, (available - width * self.page_list.count()) // 2)
        self.page_list.setViewportMargins(margin, 0, margin, 0)
        viewport = self.page_list.viewport()
        height = max(140, viewport.height()-4)
        size = QtCore.QSize(width, height)
        self.page_list.setGridSize(size)
        self.page_list.setIconSize(QtCore.QSize(width-72, max(30, height-85)))
        for row in range(self.page_list.count()):
            self.page_list.item(row).setSizeHint(size)
        self.page_list.doItemsLayout()

    def _position_card_controls(self, *_args):
        if not self._grid_ready:
            return
        item = self.page_list.currentItem()
        bounds = self.page_list.visualItemRect(item) if item is not None else QtCore.QRect()
        viewport = self.page_list.viewport().rect()
        artwork = _artwork_rect(bounds)
        restore_rect = QtCore.QRect(QtCore.QPoint(), self.restore_artwork_button.sizeHint())
        restore_rect.moveCenter(artwork.center())
        self.restore_artwork_button.setGeometry(restore_rect)
        self.restore_artwork_button.setVisible(item is not None and bool(item.data(QtCore.Qt.UserRole+6))
                                               and viewport.contains(restore_rect))
        for button, rect in (
            (self.up_button, QtCore.QRect(bounds.left()+4, artwork.center().y()-22, 28, 44)),
            (self.down_button, QtCore.QRect(bounds.right()-31, artwork.center().y()-22, 28, 44)),
            (self.page_actions_button, QtCore.QRect(bounds.right()-60, bounds.top()+21, 30, 27)),
        ):
            button.setGeometry(rect)
            button.setVisible(item is not None and viewport.contains(rect))
            button.raise_()

    def _resize_strip(self):
        self._size_grid()
        self._position_card_controls()

    def _update_workspace_layout(self):
        if not self._grid_ready:
            return
        self.name_edit.setFixedWidth(self.name_edit.fontMetrics().horizontalAdvance("Optional page name") + 32)
        transition_width = self.transition_combo.sizeHint().width()
        self.transition_combo.setFixedWidth(transition_width)
        self.pwrite_fab.set_artwork_size(128 if self.height() < 420 or self.width() < 620 else 160)
        margins = self.layout().contentsMargins()
        available = self.width() - margins.left() - margins.right()
        fields_width = sum(self._identity.itemAt(i).layout().minimumSize().width()
                           for i in range(self._identity.count())) + self._identity.spacing() * (self._identity.count() - 1)
        compact = available < fields_width + 32 + 2*(self.pwrite_fab.width()+18)
        self._identity_panel.layout().setContentsMargins(8 if compact else 16, 10, 8 if compact else 16, 10)
        if compact:
            label_width = max(label.sizeHint().width() for label in self._field_labels)
            field_width = available - 2*(self.pwrite_fab.width()+6) - 18 - label_width - 8
            self.name_edit.setFixedWidth(min(148, max(100, field_width)))
            self.transition_combo.setFixedWidth(min(transition_width, max(130, field_width)))
        for column in (0, 2):
            self._header_layout.setColumnMinimumWidth(column, self.pwrite_fab.width() + (6 if compact else 18))
        self._identity.setDirection(QtWidgets.QBoxLayout.TopToBottom if compact
                                    else QtWidgets.QBoxLayout.LeftToRight)
        self._identity_panel.layout().invalidate()
        self._identity_panel.setMinimumHeight(self._identity_panel.minimumSizeHint().height())
        self._identity_panel.updateGeometry()
        self._resize_strip()

    def add_page(self, kind):
        if len(self.session.project.pages) >= MAX_PAGES:
            raise ValueError("You’ve reached the maximum of 10 pages.")
        return super().add_page(kind)

    def duplicate_page(self, page_id):
        if len(self.session.project.pages) >= MAX_PAGES:
            raise ValueError("You’ve reached the maximum of 10 pages.")
        return super().duplicate_page(page_id)
