"""A horizontal artwork strip backed by the canonical Images session."""
from PySide6 import QtCore, QtGui, QtWidgets

from images_workspace import ImageTab as _ImagesWorkspace, PageNavigator
from project_model import PAGE_TYPES, MAX_PAGES
from prompt_writer_project import page_label


def _artwork_rect(cell):
    return cell.adjusted(36, 94, -36, -40)


class _ArtworkStrip(PageNavigator):
    import_requested = QtCore.Signal(object)
    choose_requested = QtCore.Signal(object)
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
        self._press_position = None
        self._double_click = False
        self._pending_choice = None
        self._choice_timer = QtCore.QTimer(self)
        self._choice_timer.setSingleShot(True)
        self._choice_timer.timeout.connect(self._choose_after_click)

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
        category_width = min(cell.width()-100, painter.fontMetrics().horizontalAdvance(kind)+22)
        category = QtCore.QRect(cell.right()-30-category_width, badge.top(), category_width, 26)
        tint.setAlpha(28)
        painter.setBrush(tint)
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawRoundedRect(category, 8, 8)
        painter.setPen(foreground)
        painter.drawText(category, QtCore.Qt.AlignCenter, kind)
        title_rect = QtCore.QRect(cell.x()+30, cell.y()+57, cell.width()-60, 28)
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
        painter.drawText(QtCore.QRect(cell.x()+30, cell.bottom()-33, cell.width()-60, 22),
                         QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                         painter.fontMetrics().elidedText(status, QtCore.Qt.ElideRight, cell.width()-60))
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
        for label, control in (("Page name", self.name_edit), ("Page type", self.type_combo)):
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
        identity_panel_layout.addLayout(self._identity)
        actions = QtWidgets.QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.add_button)
        actions.addStretch()
        identity_panel_layout.addLayout(actions)
        self._header_layout.addWidget(self._identity_panel, 0, 1, QtCore.Qt.AlignCenter)
        root.addLayout(self._header_layout)
        self.page_list.setItemDelegate(_ArtworkDelegate(self.page_list))
        self.page_list.setAccessibleName("Pages in order from left to right")
        self.page_list.setAccessibleDescription(
            "Click artwork to choose recent or stock images. Double-click to import. "
            "Scroll to browse. Right-click for page actions. "
            "Control plus Left or Right moves the selected page.")
        self.page_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.page_list.customContextMenuRequested.connect(self._show_page_menu)
        self.page_list.import_requested.connect(self._import_page)
        self.page_list.choose_requested.connect(self._choose_page)
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
        for key, callback in (("Return", lambda: self._choose_page(self.session.selected_page_id)),
                              ("Space", lambda: self._choose_page(self.session.selected_page_id)),
                              ("Ctrl+O", lambda: self._import_page(self.session.selected_page_id)),
                              ("Ctrl+Left", lambda: self._run(lambda: self.move_page(self.session.selected_page_id, -1))),
                              ("Ctrl+Right", lambda: self._run(lambda: self.move_page(self.session.selected_page_id, 1)))):
            action = QtGui.QAction(self.page_list)
            action.setShortcut(QtGui.QKeySequence(key))
            action.setShortcutContext(QtCore.Qt.WidgetShortcut)
            action.triggered.connect(callback)
            self.page_list.addAction(action)
        self._grid_ready = True
        for widget in (self.page_list, self.name_edit, self.type_combo, self.pwrite_fab,
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
            add("Stock Images", self._stock_selected)
            add("View All Stock Images", self._all_stock_images)
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
            add("Duplicate page", lambda: self.duplicate_page(identity), self.duplicate_button.isEnabled())
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
        self.page_list.setProperty("projectSession", self.session.session_id)
        selected_row = next((i for i, page in enumerate(self.session.project.pages)
                             if page["id"] == selected), None)
        self._selection_label.setText(f"Page {selected_row+1} of {len(self.session.project.pages)}"
                                      if selected_row is not None else "Select a page")
        for row, page in enumerate(self.session.project.pages):
            item = self.page_list.item(row)
            status = item.text().split("\n")[-1]
            if status == "Message empty":
                status = "Ready"
            item.setData(QtCore.Qt.UserRole+2, status)
            item.setData(QtCore.Qt.UserRole+3, PAGE_TYPES[page["type"]].label)
            item.setData(QtCore.Qt.UserRole+4, page["type"])
            item.setData(QtCore.Qt.UserRole+5, page.get("name") or PAGE_TYPES[page["type"]].label)
            item.setText(page_label(self.session.project, page["id"]))
            item.setToolTip(f"{item.text()}\nClick to choose recent or stock images · Double-click to import")
            item.setData(QtCore.Qt.AccessibleTextRole, f"{row+1}, {item.text()}, {status}")
        for widget in (self.heading, self.limit_label, self.duplicate_button,
                       self.delete_button, self.undo_button, self.import_button, self.stock_button,
                       self.more_button, self.animation_mode, self.animation_button):
            widget.hide()
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
        margins = self.layout().contentsMargins()
        available = self.width() - margins.left() - margins.right()
        fields_width = sum(self._identity.itemAt(i).layout().minimumSize().width()
                           for i in range(self._identity.count())) + self._identity.spacing()
        compact = available < fields_width + 32 + 2*(self.pwrite_fab.width()+18)
        self._identity_panel.layout().setContentsMargins(8 if compact else 16, 10, 8 if compact else 16, 10)
        if compact:
            label_width = max(label.sizeHint().width() for label in self._field_labels)
            field_width = available - 2*(self.pwrite_fab.width()+6) - 18 - label_width - 8
            self.name_edit.setFixedWidth(min(148, max(100, field_width)))
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
