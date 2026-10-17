"""Selected-page Images editor over the shared canonical project session."""
from __future__ import annotations

from pathlib import Path
import threading

from PIL import Image
from PySide6 import QtCore, QtGui, QtWidgets

from image_animation import INDEX_TO_SLOT, inspect_gif, normalize_gif_settings
from page_layout import PAGE_INDICES
from project_model import PAGE_TYPES, MIN_PAGES, MAX_PAGES
from project_paths import application_paths
from project_session import get_project_session
from page_artwork import PageArtworkController
from recent_media import recent_media, remember_image_copy
from ui_theme import apply_tab_heading_style


TYPE_HELP = {
    "cover": "Opening illustration with the Cover artwork prompt.",
    "illustration": "Artwork only, with no separate message overlay.",
    "letter": "Illustrated background with its own message in Messages.",
    "end_page": "Closing illustration; its prompt requests The End.",
}


class PageNavigator(QtWidgets.QListWidget):
    order_changed = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Pages — use arrows to select or Move Up and Move Down to reorder")
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.setDefaultDropAction(QtCore.Qt.MoveAction)
        self.setIconSize(QtCore.QSize(48, 60))
        self.setTextElideMode(QtCore.Qt.ElideRight)
        self.setMinimumWidth(130)
        self.setStyleSheet("QListWidget:focus { border: 2px solid palette(highlight); }")

    def dropEvent(self, event):
        super().dropEvent(event)
        self.order_changed.emit([self.item(row).data(QtCore.Qt.UserRole) for row in range(self.count())])


class _ImportWorker(QtCore.QObject):
    prepared = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(self, artwork, source, cancelled, holder):
        super().__init__()
        self.artwork, self.source, self.cancelled, self.holder = artwork, source, cancelled, holder

    @QtCore.Slot()
    def run(self):
        try:
            result = self.artwork.stage_import(self.source)
            if self.cancelled.is_set():
                result.abort()
            else:
                self.holder["result"] = result
                self.prepared.emit(result)
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.finished.emit()


class _CropDialog(QtWidgets.QDialog):
    """Pixel bounds with a live selection overlay; source files stay immutable."""
    def __init__(self, path, parent):
        super().__init__(parent)
        self.setWindowTitle("Crop artwork")
        self.resize(520, 540)
        root = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel("Position the crop using pixel bounds. The original source is retained.")
        note.setWordWrap(True)
        root.addWidget(note)
        self.original = QtGui.QPixmap(str(path))
        self.preview = QtWidgets.QLabel()
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        root.addWidget(self.preview, 1)
        form = QtWidgets.QFormLayout()
        self.bounds = []
        for label, maximum, value in (("Left", self.original.width()-1, 0),
                                       ("Top", self.original.height()-1, 0),
                                       ("Right", self.original.width(), self.original.width()),
                                       ("Bottom", self.original.height(), self.original.height())):
            control = QtWidgets.QSpinBox()
            control.setRange(0, maximum)
            control.setValue(value)
            control.setAccessibleName("Crop " + label)
            control.valueChanged.connect(self.refresh)
            self.bounds.append(control)
            form.addRow(label, control)
        root.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.ok = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        self.ok.setText("Apply Crop")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.refresh()

    def rectangle(self):
        return tuple(control.value() for control in self.bounds)

    def refresh(self):
        left, top, right, bottom = self.rectangle()
        self.ok.setEnabled(left < right and top < bottom)
        display = self.original.scaled(460, 330, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        painter = QtGui.QPainter(display)
        painter.setPen(QtGui.QPen(self.palette().highlight().color(), 3))
        scale = display.width() / max(1, self.original.width())
        painter.drawRect(QtCore.QRectF(left*scale, top*scale, (right-left)*scale, (bottom-top)*scale))
        painter.end()
        self.preview.setPixmap(display)


class ImageTab(QtWidgets.QWidget):
    image_selected = QtCore.Signal(QtGui.QPixmap)
    hover_preview_image = QtCore.Signal(QtGui.QPixmap)
    clear_preview = QtCore.Signal()
    images_changed = QtCore.Signal(str)
    image_import_completed = QtCore.Signal(int, str, str)
    artwork_import_completed = QtCore.Signal(str, str, str)
    cover_changed = QtCore.Signal()
    animation_settings_changed = QtCore.Signal(int)
    prompt_requested = QtCore.Signal(str)
    message_requested = QtCore.Signal(str)
    session_event = QtCore.Signal(object)

    def __init__(self, project_root=None, *, project_state=None, project_paths=None):
        super().__init__()
        from Image_tab import ImageAssetCard
        self.project_root = Path(project_root or application_paths().workspace_root).resolve()
        self.project_state = project_state
        self.session = get_project_session(self.project_root)
        self.artwork = PageArtworkController(self.session)
        self._shutdown = False
        self._restoring_ui = False
        self._committing = False
        self._name_dirty = False
        self._name_target = None
        self._undo = []
        self._thumbnail_cache = {}
        self._asset_warnings = {}
        self._preview_key = None
        self._image_import_thread = None
        self._image_import_worker = None
        self._import_holder = {}
        self._import_cancelled = threading.Event()
        self._pending_target = None
        self._pending_index = 0
        self._tab_active = False
        self._resource_timer = QtCore.QTimer(self)
        self._resource_timer.setInterval(1000)
        self._resource_timer.timeout.connect(self.refresh_cards)
        self.image_paths = {}
        self.labels = {}
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        self.heading = QtWidgets.QLabel("Pages")
        apply_tab_heading_style(self.heading)
        root.addWidget(self.heading)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        root.addWidget(self.splitter, 1)
        navigation = QtWidgets.QWidget()
        nav = QtWidgets.QVBoxLayout(navigation)
        nav.setContentsMargins(0, 0, 4, 0)
        self.add_button = QtWidgets.QToolButton()
        self.add_button.setText("Add Page")
        self.add_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(self.add_button)
        menu.setToolTipsVisible(True)
        for kind, definition in PAGE_TYPES.items():
            action = menu.addAction(definition.label + " — " + TYPE_HELP[kind])
            action.setToolTip(TYPE_HELP[kind])
            action.triggered.connect(lambda checked=False, kind=kind: self._run(lambda: self.add_page(kind)))
        self.add_button.setMenu(menu)
        self.add_button.setToolTip("Insert immediately after the selected page; append when none is selected.")
        nav.addWidget(self.add_button)
        self.limit_label = QtWidgets.QLabel()
        self.limit_label.setWordWrap(True)
        nav.addWidget(self.limit_label)
        self.page_list = PageNavigator()
        nav.addWidget(self.page_list, 1)
        moves = QtWidgets.QHBoxLayout()
        self.up_button = self._button("Move Up", lambda: self._run(lambda: self.move_page(self.session.selected_page_id, -1)))
        self.down_button = self._button("Move Down", lambda: self._run(lambda: self.move_page(self.session.selected_page_id, 1)))
        moves.addWidget(self.up_button)
        moves.addWidget(self.down_button)
        nav.addLayout(moves)
        actions = QtWidgets.QHBoxLayout()
        self.duplicate_button = self._button("Duplicate", lambda: self._run(lambda: self.duplicate_page(self.session.selected_page_id)))
        self.delete_button = self._button("Delete Page", lambda: self._run(lambda: self.delete_page(self.session.selected_page_id)))
        actions.addWidget(self.duplicate_button)
        actions.addWidget(self.delete_button)
        nav.addLayout(actions)
        self.undo_button = self._button("Undo Delete", lambda: self._run(self.undo_delete))
        nav.addWidget(self.undo_button)
        self.splitter.addWidget(navigation)
        workspace = QtWidgets.QWidget()
        main = QtWidgets.QVBoxLayout(workspace)
        main.setContentsMargins(4, 0, 0, 0)
        self.header = QtWidgets.QLabel()
        self.header.setWordWrap(True)
        self.header.setTextFormat(QtCore.Qt.PlainText)
        self.header.setStyleSheet("font-weight: 600;")
        main.addWidget(self.header)
        identity = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setAccessibleName("Page name")
        self.name_edit.setPlaceholderText("Optional page name")
        identity.addRow("Name", self.name_edit)
        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.setAccessibleName("Page type")
        for kind, definition in PAGE_TYPES.items():
            self.type_combo.addItem(definition.label, kind)
        self.type_combo.setToolTip("Changing a Letter to another type hides its message and retains it for restoration. Artwork and exact prompt text are preserved.")
        identity.addRow("Type", self.type_combo)
        main.addLayout(identity)
        tools = QtWidgets.QHBoxLayout()
        self.import_button = self._button("Import Image", self._browse_selected)
        self.stock_button = self._button("Stock Images", self._stock_selected)
        self.crop_button = self._button("Crop / Position", self._crop_selected)
        tools.addWidget(self.import_button)
        tools.addWidget(self.stock_button)
        tools.addWidget(self.crop_button)
        self.more_button = QtWidgets.QToolButton()
        self.more_button.setText("More")
        self.more_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        more = QtWidgets.QMenu(self.more_button)
        self.clear_action = more.addAction("Clear Image", lambda: self._run(lambda: self.clear_page_image(self.session.selected_page_id)))
        more.addAction("Open Artwork Folder", self.open_gallery_folder)
        self.more_button.setMenu(more)
        tools.addWidget(self.more_button)
        main.addLayout(tools)
        self.empty_label = QtWidgets.QLabel("Import an image or choose stock artwork for this page.")
        self.empty_label.setWordWrap(True)
        main.addWidget(self.empty_label)
        self.card = ImageAssetCard(0, "", self.project_root, self)
        self.card.setProperty("workspacePreview", True)
        self.card.title_label.hide()
        self.card.clear_btn.hide()
        self.card.settings_btn.hide()
        self.card.setMinimumSize(160, 140)
        self.card.setMaximumSize(16777215, 16777215)
        self.card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.card.set_playback_active(False)
        main.addWidget(self.card, 1)
        self.card.select_requested.connect(self._browse_selected)
        self.card.file_dropped.connect(lambda _index, path: self._run(lambda: self.import_image(self.session.selected_page_id, path)))
        animation = QtWidgets.QHBoxLayout()
        self.animation_mode = QtWidgets.QComboBox()
        self.animation_mode.setAccessibleName("GIF playback mode")
        for label, value in (("Original", "original"), ("Loop", "loop"), ("Ping-Pong", "ping_pong")):
            self.animation_mode.addItem(label, value)
        self.animation_button = self._button("Animation Settings", self._animation_selected)
        animation.addWidget(self.animation_mode)
        animation.addWidget(self.animation_button)
        animation.addStretch()
        main.addLayout(animation)
        routes = QtWidgets.QHBoxLayout()
        self.pwrite_fab = self._button("Write Image Prompt", self.request_prompt)
        self.message_button = self._button("Edit Letter Text", self.request_message)
        routes.addWidget(self.pwrite_fab)
        routes.addWidget(self.message_button)
        routes.addStretch()
        main.addLayout(routes)
        self.splitter.addWidget(workspace)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([240, 680])
        self.splitter.setChildrenCollapsible(False)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.PlainText)
        root.addWidget(self.status)
        self.page_list.currentItemChanged.connect(self._selection_changed)
        self.page_list.order_changed.connect(lambda ids: self._run(lambda: self.reorder_pages(ids)))
        self.name_edit.textEdited.connect(self._edit_name)
        self.name_edit.editingFinished.connect(lambda: self._run(self.flush_pending_edits))
        self.type_combo.activated.connect(lambda: self._run(lambda: self.change_page_type(self.session.selected_page_id, self.type_combo.currentData())))
        self.animation_mode.activated.connect(lambda: self._run(self._set_animation_mode))
        self.session_event.connect(self._session_changed)
        self.session.add_listener(self.session_event.emit)
        if self.session.selected_page_id is None and self.session.project.pages:
            self.session.select_page(self.session.project.pages[0]["id"])
        self.refresh_cards()

    def _button(self, text, callback):
        button = QtWidgets.QPushButton(text)
        button.setAccessibleName(text)
        button.clicked.connect(lambda: self._run(callback))
        return button

    def _run(self, callback):
        try:
            return callback()
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            self.status.setText(str(error))
            return False

    def _selection_changed(self, current, previous):
        if self._restoring_ui:
            return
        identity = current.data(QtCore.Qt.UserRole) if current else None
        if self._run(self.flush_pending_edits) is False:
            self.refresh_cards()
            return
        self.session.select_page(identity)

    def _edit_name(self, text):
        # Authored text enters the shared model immediately, so Save never sees
        # a stale name even while focus remains in this field.
        if self._restoring_ui or self._name_target is None:
            return
        self._name_dirty = True
        target = self._name_target
        if self.session.apply_result(target, lambda model, identity: model.rename_page(identity, text)):
            self._name_target = self.session.capture_target(target.page_id, fields=("name",), include_shared=False)
            self.images_changed.emit("page_name")
        else:
            self._name_dirty = False
            self.status.setText("The original page changed; its name was not overwritten.")

    @QtCore.Slot(object)
    def _session_changed(self, event):
        if self._shutdown or event.session_id != self.session.session_id:
            return
        if event.kind == "project_replaced":
            self._import_cancelled.set()
            self._name_dirty = False
            self._undo.clear()
            self._thumbnail_cache.clear()
            self.card.release_asset_handle()
            self._preview_key = None
        elif event.kind == "selection" and self._name_dirty:
            self.flush_pending_edits()
        if not self._committing and not self.session.is_restoring:
            self.refresh_cards()

    def _asset_info(self, page):
        path = self.artwork.resolve_asset(page["id"])
        if path is None:
            return None, False, None
        stat = path.stat()
        from transactional_io import file_change_token
        key = (self.session.session_id, page["id"], page["artwork"].get("asset"), stat.st_size, stat.st_mtime_ns, file_change_token(path, stat_result=stat))
        cached = self._thumbnail_cache.get(page["id"])
        if cached is None or cached[0] != key:
            with Image.open(path) as media:
                animated = media.format == "GIF" and getattr(media, "n_frames", 1) > 1
                media.verify()
            reader = QtGui.QImageReader(str(path))
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(64, 80, QtCore.Qt.KeepAspectRatio))
            pixmap = QtGui.QPixmap.fromImage(reader.read())
            after = path.stat()
            if pixmap.isNull() or (after.st_size, after.st_mtime_ns, file_change_token(path, stat_result=after)) != key[3:]:
                raise ValueError("Artwork is still being written or cannot be decoded")
            cached = (key, animated, QtGui.QIcon(pixmap), path)
            self._thumbnail_cache[page["id"]] = cached
        return path, cached[1], cached[2]

    def refresh_cards(self):
        if self._shutdown:
            return False
        pages = self.session.project.pages
        selected = self.session.selected_page_id
        issues = self.session.project.validate()
        self._restoring_ui = True
        blocker = QtCore.QSignalBlocker(self.page_list)
        scroll = self.page_list.verticalScrollBar().value()
        old_ids = [self.page_list.item(row).data(QtCore.Qt.UserRole) for row in range(self.page_list.count())]
        rebuild = old_ids != [page["id"] for page in pages]
        if rebuild:
            self.page_list.clear()
        self.image_paths.clear()
        self.labels.clear()
        active_ids = {page["id"] for page in pages}
        self._thumbnail_cache = {identity: value for identity, value in self._thumbnail_cache.items() if identity in active_ids}
        self._asset_warnings = {identity: value for identity, value in self._asset_warnings.items() if identity in active_ids}
        selected_page = None
        selected_media = (None, False, None)
        for number, page in enumerate(pages, 1):
            try:
                media = self._asset_info(page)
                if media[0] is None and page["artwork"].get("asset"):
                    raise ValueError("Referenced artwork is missing")
                self._asset_warnings.pop(page["id"], None)
            except (OSError, ValueError, KeyError):
                media = (None, False, None)
                cached = self._thumbnail_cache.get(page["id"])
                if cached and cached[0][:3] == (self.session.session_id, page["id"], page["artwork"].get("asset")):
                    media = (cached[3], cached[1], cached[2])
                self._asset_warnings[page["id"]] = "Artwork unavailable — preview out of date"
            status = []
            if page["id"] in self._asset_warnings:
                status.append(self._asset_warnings[page["id"]])
            if media[0] is None:
                status.append("Image missing")
            if any(issue.page_id == page["id"] and issue.code == "empty_message" for issue in issues):
                status.append("Message empty")
            label = PAGE_TYPES[page["type"]].label
            title = f"{number}. {page['name'] or label} · {label}"
            item = QtWidgets.QListWidgetItem() if rebuild else self.page_list.item(number-1)
            item.setText(title + "\n" + (", ".join(status) or "Ready"))
            item.setData(QtCore.Qt.UserRole, page["id"])
            item.setToolTip(title + "\n" + (", ".join(status) or "Ready"))
            item.setSizeHint(QtCore.QSize(160, 72))
            item.setIcon(media[2] or QtGui.QIcon())
            item.setData(QtCore.Qt.AccessibleTextRole, item.text())
            if rebuild:
                self.page_list.addItem(item)
            key = page["recovery"].get("legacy_key")
            index = PAGE_INDICES.get(key)
            if index is not None:
                self.image_paths[index] = str(media[0]) if media[0] else None
                self.labels[index] = (title, key + ".png")
            if page["id"] == selected:
                self.page_list.setCurrentItem(item)
                item.setData(QtCore.Qt.AccessibleTextRole, "Selected " + item.text())
                selected_page, selected_media = page, media
        if selected is None:
            self.page_list.setCurrentRow(-1)
        if rebuild:
            self.page_list.verticalScrollBar().setValue(scroll)
        del blocker
        count = len(pages)
        self.limit_label.setText(f"{count} / {MAX_PAGES} pages" + (" — limit reached" if count >= MAX_PAGES else ""))
        self.add_button.setEnabled(count < MAX_PAGES)
        self.add_button.setToolTip(f"Maximum {MAX_PAGES} pages reached. Existing pages remain editable." if count >= MAX_PAGES else "Insert after the selected page, or append when none is selected.")
        self.duplicate_button.setEnabled(selected_page is not None and count < MAX_PAGES)
        self.delete_button.setEnabled(selected_page is not None and count > MIN_PAGES)
        self.delete_button.setToolTip(f"Keep at least {MIN_PAGES} pages. Delete includes the page's artwork reference, prompt, and message; source files are retained.")
        row = self.page_list.currentRow()
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < count-1)
        self.undo_button.setEnabled(bool(self._undo) and count < MAX_PAGES)
        for control in (self.name_edit, self.type_combo, self.import_button, self.stock_button, self.pwrite_fab):
            control.setEnabled(selected_page is not None)
        self.more_button.setEnabled(selected_page is not None)
        path, animated, _icon = selected_media
        self.clear_action.setEnabled(selected_page is not None and bool(selected_page["artwork"].get("asset")))
        self.crop_button.setEnabled(path is not None and not animated)
        self.crop_button.setToolTip("Animated GIF cropping is not supported; its source animation is preserved." if animated else "Adjust the crop position and size without changing the original source.")
        self.animation_button.setVisible(animated)
        self.animation_mode.setVisible(animated)
        self.message_button.setEnabled(selected_page is not None and selected_page["type"] == "letter")
        self.message_button.setVisible(selected_page is not None and selected_page["type"] == "letter")
        self.empty_label.setVisible(path is None)
        self.import_button.setText("Replace Image" if path else "Import Image")
        self.import_button.setAccessibleName(self.import_button.text())
        if selected_page is None:
            self.header.setText("No page selected — use Add Page to begin.")
            self.name_edit.clear()
            self._name_target = None
            self.card.clear_pixmap()
            self._preview_key = None
        else:
            name = selected_page["name"] or PAGE_TYPES[selected_page["type"]].label
            self.header.setText(f"Page {row+1} of {count} — {name} · {PAGE_TYPES[selected_page['type']].label}")
            if not self._name_dirty or self._name_target is None or self._name_target.page_id != selected:
                self.name_edit.setText(selected_page["name"])
                self._name_target = self.session.capture_target(selected, fields=("name",), include_shared=False)
                self._name_dirty = False
            self.type_combo.setCurrentIndex(self.type_combo.findData(selected_page["type"]))
            settings = normalize_gif_settings(selected_page["artwork"].get("animation"))
            self.animation_mode.setCurrentIndex(self.animation_mode.findData(settings["playback_mode"]))
            cached = self._thumbnail_cache.get(selected)
            preview_key = (self.session.session_id, selected, repr(selected_page["artwork"]), str(path), cached[0] if cached else None)
            if preview_key != self._preview_key:
                self.card.release_asset_handle()
                if path is None:
                    self.card.clear_pixmap()
                    self.clear_preview.emit()
                else:
                    self.card.set_asset_path(str(path), animated_gif=animated, settings=settings, animate_gif=self._tab_active)
                    self.image_selected.emit(QtGui.QPixmap(str(path)))
                self._preview_key = preview_key
        self.card.clear_btn.hide()
        self.card.settings_btn.hide()
        self._restoring_ui = False
        return True

    def _commit(self, reason):
        self._committing = True
        try:
            # Merge only changed legacy fields before refreshing disposable views.
            from project_consumers import project_legacy_views
            self.session.synchronize_workspace()
            count = len(self.session.project.pages)
            if MIN_PAGES <= count <= MAX_PAGES:
                project_legacy_views(self.session)
            self.session.save(sync_workspace=False)
            self.status.setText("")
            return True
        except (OSError, ValueError, RuntimeError) as error:
            self.status.setText(f"Change retained in this session; saving needs attention: {error}")
            return False
        finally:
            self._committing = False
            self.refresh_cards()
            self.images_changed.emit(reason)
            self.cover_changed.emit()

    def flush_pending_edits(self):
        if not self._name_dirty or self._name_target is None:
            return True
        text, target = self.name_edit.text(), self._name_target
        if self.session.apply_result(target, lambda model, identity: model.rename_page(identity, text)):
            if not self._commit("page_name"):
                return False
            self._name_dirty = False
            return True
        self.status.setText("The original page changed; its pending name was not applied.")
        return False

    def _prepare_page_operation(self):
        if not self.flush_pending_edits():
            raise RuntimeError("Save or repair the pending page name before changing pages.")
        # Legacy editors may have completed writes since the last projection.
        # Merge before a duplicate/delete/type operation captures its content.
        self.session.synchronize_workspace()

    def add_page(self, kind):
        self._prepare_page_operation()
        ids = [page["id"] for page in self.session.project.pages]
        selected = self.session.selected_page_id
        index = ids.index(selected)+1 if selected in ids else None
        identity = self.session.project.add_page(kind, name=PAGE_TYPES[kind].label, index=index)
        self.session.select_page(identity)
        self._commit("page_added")
        return identity

    def rename_page(self, page_id, name):
        self._prepare_page_operation()
        self.session.project.rename_page(page_id, name)
        self._commit("page_name")

    def reorder_pages(self, identities):
        self._prepare_page_operation()
        self.session.project.reorder_pages(identities)
        self._commit("page_order")

    def move_page(self, page_id, delta):
        self._prepare_page_operation()
        identities = [page["id"] for page in self.session.project.pages]
        index = identities.index(page_id)
        target = index + delta
        if 0 <= target < len(identities):
            identities[index], identities[target] = identities[target], identities[index]
            self.reorder_pages(identities)

    def duplicate_page(self, page_id):
        self._prepare_page_operation()
        identity = self.session.project.duplicate_page(page_id)
        self.session.select_page(identity)
        self._commit("page_duplicated")
        return identity

    def change_page_type(self, page_id, kind):
        self._prepare_page_operation()
        self.session.project.change_page_type(page_id, kind)
        self._commit("page_type")

    def delete_page(self, page_id, confirm=True):
        self._prepare_page_operation()
        page = self.session.project.get_page(page_id)
        self.session.project.deletion_scope(page_id)
        target = self.session.capture_target(page_id, include_shared=False)
        if confirm:
            content = "artwork reference and image prompt"
            if page["message"] is not None or page["recovery"].get("inactive_message"):
                content += ", including its associated message"
            name = page["name"] or PAGE_TYPES[page["type"]].label
            answer = QtWidgets.QMessageBox.question(self, "Delete Page", f'Delete “{name}” and its {content}?\n\nUndo Delete restores this page. Source files are retained.', QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Cancel)
            if answer != QtWidgets.QMessageBox.Yes:
                return False
        def remove(model, identity):
            self._undo.append(model.delete_page(identity))
        if not self.session.apply_result(target, remove):
            return False
        self._commit("page_deleted")
        return True

    def undo_delete(self):
        if not self._undo:
            return False
        self._prepare_page_operation()
        identity = self.session.project.restore_page(self._undo[-1])
        self._undo.pop()
        self.session.select_page(identity)
        self._commit("page_restored")
        return True

    def clear_page_image(self, page_id):
        self._prepare_page_operation()
        if self.artwork.clear_image(page_id):
            self._commit("image_cleared")
            return True
        return False

    def _legacy_page_id(self, index):
        key = INDEX_TO_SLOT.get(index)
        return next((page["id"] for page in self.session.project.pages if page["recovery"].get("legacy_key") == key), None) if key else None

    def set_image_path(self, index, source_path):
        identity = self._legacy_page_id(index)
        return bool(identity and self.import_image(identity, source_path))

    def import_image(self, page_id, source_path, target=None):
        if self._shutdown or self._image_import_thread is not None or not source_path:
            return False
        self._prepare_page_operation()
        target = target or self.artwork.capture_target(page_id)
        if target.page_id != page_id or not self.session.target_is_current(target):
            return False
        self._pending_target = target
        page = self.session.project.get_page(page_id)
        self._pending_index = PAGE_INDICES.get(page["recovery"].get("legacy_key"), 0)
        self._import_cancelled = threading.Event()
        self._import_holder = {}
        thread = QtCore.QThread(self)
        worker = _ImportWorker(self.artwork, source_path, self._import_cancelled, self._import_holder)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.prepared.connect(self._import_prepared, QtCore.Qt.QueuedConnection)
        worker.failed.connect(self._import_failed, QtCore.Qt.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit, QtCore.Qt.DirectConnection)
        thread.finished.connect(self._import_finished, QtCore.Qt.QueuedConnection)
        thread.finished.connect(thread.deleteLater)
        self._image_import_thread, self._image_import_worker = thread, worker
        self.status.setText("Processing image…")
        thread.start()
        return True

    @QtCore.Slot(object)
    def _import_prepared(self, result):
        if self._import_holder.pop("result", None) is not result:
            result.abort()
            return
        target = self._pending_target
        if self._shutdown or self._import_cancelled.is_set() or target is None:
            result.abort()
            return
        try:
            if not self.artwork.apply_import(target, result):
                self._import_failed("The original page changed or its project was replaced; image discarded.")
                return
            self._commit("image_imported")
            path = str(self.artwork.resolve_asset(target.page_id))
            try:
                remember_image_copy(self.project_root, target.project_id, target.page_id, path, result.source_path.name)
            except (OSError, ValueError):
                # History is a convenience cache; a failure cannot roll back a
                # successfully validated canonical artwork change.
                import logging
                logging.getLogger(__name__).exception("Could not retain recent page artwork")
            self.image_import_completed.emit(self._pending_index, path, "")
            self.artwork_import_completed.emit(target.page_id, path, "")
        except Exception as error:
            result.abort()
            self._import_failed(str(error))

    @QtCore.Slot(str)
    def _import_failed(self, message):
        if not self._shutdown:
            self.status.setText(message)
            identity = self._pending_target.page_id if self._pending_target else ""
            self.image_import_completed.emit(self._pending_index, "", message)
            self.artwork_import_completed.emit(identity, "", message)

    @QtCore.Slot()
    def _import_finished(self):
        thread = self._image_import_thread
        if thread is None or self.sender() is not thread:
            return
        # finished may arrive before native thread-local cleanup completes.
        # Join before dropping Python wrappers for objects owned by that thread.
        thread.wait()
        self._image_import_thread = None
        self._image_import_worker = None
        result = self._import_holder.pop("result", None)
        if result is not None:
            result.abort()

    def _browse_selected(self, *_args):
        from Image_tab import _downloads_directory
        self._prepare_page_operation()
        identity = self.session.selected_page_id
        if identity is None:
            return
        target = self.artwork.capture_target(identity)
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(self, "Import Image", _downloads_directory(), "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
        if path:
            self._run(lambda: self.import_image(identity, path, target=target))

    def _stock_selected(self):
        from Image_tab import StockImageDialog, STOCK_IMAGE_FILES, _downloads_directory
        self._prepare_page_operation()
        identity = self.session.selected_page_id
        if identity is None:
            return
        target = self.artwork.capture_target(identity)
        page = self.session.project.get_page(identity)
        slot = PAGE_TYPES[page["type"]].artwork_template
        directory = application_paths(self.project_root).stock_images_root / slot
        files = tuple(path for name in STOCK_IMAGE_FILES[PAGE_INDICES[slot]] if (path := directory / name).is_file())
        history = recent_media(self.project_root, self.session.project.project_id, f"image:{identity}")
        if not history and page["recovery"].get("legacy_key"):
            history = recent_media(self.project_root, self.session.project.project_id, f"image:{page['recovery']['legacy_key']}")
        recent = tuple((Path(value), label) for value, label in history if Path(value).is_file())
        dialog = StockImageDialog(PAGE_TYPES[page["type"]].label, files, self, allow_browse=True,
                                  recent_paths=tuple(path for path, label in recent),
                                  recent_labels=tuple(label for path, label in recent))
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        if dialog.browse_requested:
            path, _filter = QtWidgets.QFileDialog.getOpenFileName(self, "Import Image", _downloads_directory(), "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
        else:
            path = dialog.selected_path()
        if path:
            self._run(lambda: self.import_image(identity, str(path), target=target))

    def _crop_selected(self):
        self._prepare_page_operation()
        identity = self.session.selected_page_id
        if identity is None:
            return
        target = self.artwork.capture_target(identity)
        path = self.artwork.resolve_asset(identity)
        if path is None:
            return
        dialog = _CropDialog(path, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            def apply():
                if self.artwork.crop(identity, dialog.rectangle(), target=target):
                    self._commit("image_cropped")
            self._run(apply)

    def _set_animation_mode(self):
        self._prepare_page_operation()
        identity = self.session.selected_page_id
        if identity is None:
            return
        settings = normalize_gif_settings(self.session.project.get_page(identity)["artwork"]["animation"])
        settings["playback_mode"] = self.animation_mode.currentData()
        self.session.project.update_artwork(identity, {"animation": settings})
        self._commit("animation")
        self.animation_settings_changed.emit(PAGE_INDICES.get(self.session.project.get_page(identity)["recovery"].get("legacy_key"), 0))

    def _animation_selected(self):
        from Image_tab import ImageSettingsDialog
        self._prepare_page_operation()
        identity = self.session.selected_page_id
        if identity is None:
            return
        target = self.artwork.capture_target(identity)
        path = self.artwork.resolve_asset(identity)
        if path is None:
            return
        page = self.session.project.get_page(identity)
        def preview(settings):
            if self.session.target_is_current(target) and self.session.selected_page_id == identity:
                self.card.set_asset_path(str(path), animated_gif=True, settings=settings)
                self.card.settings_btn.hide()
        dialog = ImageSettingsDialog(page["name"] or PAGE_TYPES[page["type"]].label, animated_gif=True,
                                     settings=page["artwork"]["animation"], embedded_play_count=inspect_gif(path).embedded_play_count,
                                     preview_callback=preview, parent=self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            if self.session.apply_result(target, lambda model, identity: model.update_artwork(identity, {"animation": dialog.gif_settings()})):
                self._commit("animation")
                self.animation_settings_changed.emit(PAGE_INDICES.get(page["recovery"].get("legacy_key"), 0))
        self._preview_key = None
        self.refresh_cards()

    def request_prompt(self):
        if not self.flush_pending_edits():
            return
        if self.session.selected_page_id is not None:
            self.prompt_requested.emit(self.session.selected_page_id)

    def request_message(self):
        if not self.flush_pending_edits():
            return
        identity = self.session.selected_page_id
        if identity and self.session.project.get_page(identity)["type"] == "letter":
            self.message_requested.emit(identity)

    def focus_asset_slot(self, target):
        identity = next((page["id"] for page in self.session.project.pages if target in (page["id"], page["recovery"].get("legacy_key"))), None)
        if identity:
            self.flush_pending_edits()
            self.session.select_page(identity)
            self.page_list.setFocus()

    def refresh_from_disk(self):
        if self.session.is_restoring:
            return False
        return self.refresh_cards()

    def sync_from_disk(self, *, force=False):
        return self.refresh_from_disk()

    def sync_to_disk(self):
        self.flush_pending_edits()
        self.session.save(sync_workspace=False)
        return True

    def has_meaningful_images(self):
        return any(page["artwork"].get("asset") for page in self.session.project.pages)

    def open_gallery_folder(self):
        path = self.project_root / "artwork"
        path.mkdir(exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def reset_project_images(self):
        # New Project is owned by the foundation; the shell has already reset it.
        self._undo.clear()
        self._thumbnail_cache.clear()
        self._preview_key = None
        self.refresh_cards()

    def activate_for_tab_change(self):
        self._tab_active = True
        self.refresh_from_disk()
        self.card.set_playback_active(True)
        self._resource_timer.start()

    def deactivate_for_tab_change(self):
        self.flush_pending_edits()
        self._tab_active = False
        self.card.set_playback_active(False)
        self._resource_timer.stop()

    def set_compact_layout(self, compact):
        self.splitter.setOrientation(QtCore.Qt.Vertical if compact and self.width() < 560 else QtCore.Qt.Horizontal)

    def minimumSizeHint(self):
        return QtCore.QSize(480, 320)

    def heightForWidth(self, width):
        return 530 if width < 640 else 460

    def apply_theme_assets(self, theme_service=None):
        self.card.apply_theme_assets(theme_service)

    def prepare_for_project_restore(self, timeout_ms=5000):
        self._resource_timer.stop()
        self.flush_pending_edits()
        if not self._stop_import(timeout_ms):
            raise RuntimeError("Wait for the image import to finish before replacing the project.")
        self.card.release_asset_handle()
        self._preview_key = None

    def _stop_import(self, timeout_ms):
        self._import_cancelled.set()
        thread = self._image_import_thread
        if thread is not None and not thread.wait(timeout_ms if timeout_ms is not None else 5000):
            return False
        result = self._import_holder.pop("result", None)
        if result is not None:
            result.abort()
        return True

    def shutdown(self, timeout_ms=5000):
        if self._shutdown:
            return True
        self.flush_pending_edits()
        if not self._stop_import(timeout_ms):
            return False
        self._shutdown = True
        self._resource_timer.stop()
        self.session.remove_listener(self.session_event.emit)
        self.card.release_asset_handle()
        self._thumbnail_cache.clear()
        return True

    def closeEvent(self, event):
        if self.shutdown():
            super().closeEvent(event)
        else:
            event.ignore()
