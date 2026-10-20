"""A horizontal artwork strip backed by the canonical Images session."""
from PySide6 import QtCore, QtGui, QtWidgets

from images_workspace import ImageTab as _ImagesWorkspace, PageNavigator
from project_model import PAGE_TYPES, MAX_PAGES
from prompt_writer_project import page_label


def _artwork_rect(cell):
    return cell.adjusted(36, 67, -36, -18)


class _ArtworkStrip(PageNavigator):
    import_requested = QtCore.Signal(object)
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
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setStyleSheet("QListWidget { border: none; background: transparent; }")
        self.viewport().setCursor(QtCore.Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._wheel_remainder = 0.0

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
        item = self.itemAt(event.position().toPoint())
        if (event.button() == QtCore.Qt.LeftButton and item is not None
                and _artwork_rect(self.visualItemRect(item)).contains(event.position().toPoint())):
            identity = item.data(QtCore.Qt.UserRole)
            self.setCurrentItem(item)
            self.import_requested.emit(identity)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.geometry_changed.emit()


class _ArtworkDelegate(QtWidgets.QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        cell, palette = option.rect, option.palette
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        foreground = palette.text().color()
        muted = QtGui.QColor(foreground)
        muted.setAlpha(155)
        if index.row() < index.model().rowCount() - 1:
            gradient = QtGui.QLinearGradient(cell.right(), cell.top(), cell.right(), cell.bottom())
            transparent = QtGui.QColor(foreground)
            transparent.setAlpha(0)
            line = QtGui.QColor(foreground)
            line.setAlpha(65)
            for stop, color in ((0, transparent), (.23, line), (.77, line), (1, transparent)):
                gradient.setColorAt(stop, color)
            painter.setPen(QtGui.QPen(QtGui.QBrush(gradient), 1))
            painter.drawLine(cell.topRight(), cell.bottomRight())
        font = QtGui.QFont(option.font)
        font.setBold(selected)
        painter.setFont(font)
        painter.setPen(palette.highlight().color() if selected else muted)
        painter.drawText(cell.adjusted(17, 11, -17, -cell.height()+39),
                         QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, str(index.row()+1))
        if selected:
            painter.fillRect(QtCore.QRect(cell.x()+17, cell.y()+40, 13, 2), palette.highlight())
        painter.setPen(foreground)
        title_rect = cell.adjusted(43, 9, -20, -cell.height()+34)
        painter.drawText(title_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(index.data(QtCore.Qt.DisplayRole) or "",
                                                         QtCore.Qt.ElideRight, title_rect.width()))
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(muted)
        painter.drawText(cell.adjusted(43, 34, -20, -cell.height()+57),
                         QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                         index.data(QtCore.Qt.UserRole+3) or "")
        art = _artwork_rect(cell)
        icon = index.data(QtCore.Qt.DecorationRole)
        if icon is not None and not icon.isNull():
            icon.paint(painter, art, QtCore.Qt.AlignCenter)
        else:
            painter.setPen(QtGui.QPen(muted, 1, QtCore.Qt.DashLine))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRoundedRect(art.adjusted(1, 1, -1, -1), 3, 3)
            painter.drawText(art.adjusted(12, 8, -12, -8),
                             QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap,
                             "Double-click to import an image")
        status = index.data(QtCore.Qt.UserRole+2) or ""
        if status not in ("Ready", "Image missing"):
            painter.setPen(muted)
            painter.drawText(cell.adjusted(36, cell.height()-18, -36, 0), QtCore.Qt.AlignCenter,
                             painter.fontMetrics().elidedText(status, QtCore.Qt.ElideRight, art.width()))
        painter.restore()


class ImageTab(_ImagesWorkspace):
    navigator_class = _ArtworkStrip

    def __init__(self, *args, **kwargs):
        self._grid_ready = False
        self._last_strip_selection = None
        super().__init__(*args, **kwargs)
        root = self.layout()
        while root.count():
            item = root.takeAt(0)
            if item.widget():
                item.widget().hide()
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(18)
        top.addWidget(self.pwrite_fab, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.pwrite_fab.set_surface(self)
        self._identity = QtWidgets.QBoxLayout(QtWidgets.QBoxLayout.LeftToRight)
        self._identity.setSpacing(12)
        self._field_labels = []
        for label, control in (("Page name", self.name_edit), ("Page type", self.type_combo)):
            column = QtWidgets.QHBoxLayout()
            column.setSpacing(8)
            column.setAlignment(QtCore.Qt.AlignVCenter)
            field_label = QtWidgets.QLabel(label)
            field_label.setBuddy(control)
            self._field_labels.append(field_label)
            column.addWidget(field_label)
            column.addWidget(control)
            self._identity.addLayout(column)
        self.name_edit.setMinimumWidth(0)
        self.type_combo.setFixedWidth(130)
        top.addLayout(self._identity)
        top.addStretch(1)
        root.addLayout(top)
        self.page_list.setItemDelegate(_ArtworkDelegate(self.page_list))
        self.page_list.setAccessibleName("Pages in order from left to right")
        self.page_list.setAccessibleDescription(
            "Scroll to browse. Double-click artwork to import. Right-click for page actions. "
            "Control plus Left or Right moves the selected page.")
        self.page_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.page_list.customContextMenuRequested.connect(self._show_page_menu)
        self.page_list.import_requested.connect(self._import_page)
        root.addWidget(self.page_list, 1)
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
        self.page_list.horizontalScrollBar().valueChanged.connect(self._position_move_buttons)
        self.page_list.geometry_changed.connect(self._resize_strip)
        for key, callback in (("Return", lambda: self._import_page(self.session.selected_page_id)),
                              ("Ctrl+Left", lambda: self._run(lambda: self.move_page(self.session.selected_page_id, -1))),
                              ("Ctrl+Right", lambda: self._run(lambda: self.move_page(self.session.selected_page_id, 1)))):
            action = QtGui.QAction(self.page_list)
            action.setShortcut(QtGui.QKeySequence(key))
            action.setShortcutContext(QtCore.Qt.WidgetShortcut)
            action.triggered.connect(callback)
            self.page_list.addAction(action)
        self._grid_ready = True
        for widget in (self.page_list, self.name_edit, self.type_combo, self.pwrite_fab, self.status):
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
            add("Import image…", self._browse_selected)
            add("Stock & recent images…", self._stock_selected)
            add("Crop image…", self._crop_selected, self.crop_button.isEnabled())
            cached = self._thumbnail_cache.get(identity)
            if cached and cached[1]:
                add("Animation settings…", self._animation_selected)
            add("Clear page artwork", lambda: self.clear_page_image(identity), bool(page["artwork"].get("asset")))
            menu.addSeparator()
            add("Prompt Writer", self.request_prompt)
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
        if identity is not None:
            add("Delete page…", lambda: self.delete_page(identity), self.delete_button.isEnabled())
        if self._undo:
            action = menu.addAction("Undo delete")
            action.setEnabled(self.undo_button.isEnabled())
            action.triggered.connect(lambda checked=False: invoke(self.undo_delete, False))
        return menu

    def _show_page_menu(self, position):
        item = self.page_list.itemAt(position)
        identity = item.data(QtCore.Qt.UserRole) if item else None
        if identity is not None and not self._select_for_action(identity):
            return
        menu = self._page_menu(identity)
        menu.exec(self.page_list.viewport().mapToGlobal(position))
        menu.deleteLater()

    def _show_background_menu(self, position):
        menu = self._page_menu(self.session.selected_page_id)
        menu.exec(self.mapToGlobal(position))
        menu.deleteLater()

    def refresh_cards(self):
        offset = self.page_list.horizontalScrollBar().value() if self._grid_ready else 0
        selected = self.session.selected_page_id
        old_order = [self.page_list.item(row).data(QtCore.Qt.UserRole)
                     for row in range(self.page_list.count())]
        result = super().refresh_cards()
        if not self._grid_ready:
            return result
        for row, page in enumerate(self.session.project.pages):
            item = self.page_list.item(row)
            status = item.text().split("\n")[-1]
            if status == "Message empty":
                status = "Ready"
            item.setData(QtCore.Qt.UserRole+2, status)
            item.setData(QtCore.Qt.UserRole+3, PAGE_TYPES[page["type"]].label)
            item.setText(page_label(self.session.project, page["id"]))
            item.setToolTip(f"{row+1} · {item.text()}\nDouble-click to import · Right-click for page actions")
            item.setData(QtCore.Qt.AccessibleTextRole, f"{row+1}, {item.text()}, {status}")
        for widget in (self.heading, self.limit_label, self.add_button, self.duplicate_button,
                       self.delete_button, self.undo_button, self.import_button, self.stock_button,
                       self.crop_button, self.more_button, self.animation_mode, self.animation_button):
            widget.hide()
        self.add_button.setEnabled(True)
        self._size_grid()
        if (selected == self._last_strip_selection
                and old_order == [page["id"] for page in self.session.project.pages]):
            self.page_list.horizontalScrollBar().setValue(offset)
        elif self.page_list.currentItem() is not None:
            self.page_list.scrollToItem(self.page_list.currentItem())
        self._last_strip_selection = selected
        self._position_move_buttons()
        return result

    def _size_grid(self):
        if not self._grid_ready:
            return
        viewport = self.page_list.viewport()
        width = max(210, min(390, viewport.width() // 3))
        height = max(140, viewport.height()-4)
        size = QtCore.QSize(width, height)
        self.page_list.setGridSize(size)
        self.page_list.setIconSize(QtCore.QSize(width-72, max(30, height-85)))
        for row in range(self.page_list.count()):
            self.page_list.item(row).setSizeHint(size)
        self.page_list.doItemsLayout()

    def _position_move_buttons(self, *_args):
        if not self._grid_ready:
            return
        item = self.page_list.currentItem()
        bounds = self.page_list.visualItemRect(item) if item is not None else QtCore.QRect()
        viewport = self.page_list.viewport().rect()
        for button, x in ((self.up_button, bounds.left()+4), (self.down_button, bounds.right()-31)):
            rect = QtCore.QRect(x, _artwork_rect(bounds).center().y()-22, 28, 44)
            button.setGeometry(rect)
            button.setVisible(item is not None and viewport.contains(rect))
            button.raise_()

    def _resize_strip(self):
        self._size_grid()
        self._position_move_buttons()

    def _update_workspace_layout(self):
        if not self._grid_ready:
            return
        self.name_edit.setFixedWidth(self.name_edit.fontMetrics().horizontalAdvance("Optional page name") + 32)
        self.pwrite_fab.set_artwork_size(128 if self.height() < 420 or self.width() < 620 else 160)
        self._identity.setDirection(QtWidgets.QBoxLayout.TopToBottom if self.width() < 760
                                    else QtWidgets.QBoxLayout.LeftToRight)
        self._resize_strip()

    def add_page(self, kind):
        if len(self.session.project.pages) >= MAX_PAGES:
            raise ValueError("You’ve reached the maximum of 10 pages.")
        return super().add_page(kind)

    def duplicate_page(self, page_id):
        if len(self.session.project.pages) >= MAX_PAGES:
            raise ValueError("You’ve reached the maximum of 10 pages.")
        return super().duplicate_page(page_id)
