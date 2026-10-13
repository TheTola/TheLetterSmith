"""Shared page count, type, ordering, and horizontal navigation controls."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from weakref import WeakValueDictionary

from PySide6 import QtCore, QtWidgets

from page_layout import (
    MAX_PAGES, MIN_PAGES, load_pages, page_labels, resized_pages, save_pages,
)
from project_model import PAGE_TYPES


class PageLayoutController(QtCore.QObject):
    changed = QtCore.Signal(bool)
    selection_changed = QtCore.Signal(str)
    session_event = QtCore.Signal(object)

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.compatibility_error = ""
        self._refreshing = False
        self._session = None
        self.directory = root / "gallery/user/pages"
        self.pages = load_pages(self.directory)
        self.selected = self.pages[0].key
        self.change_guard = None
        self.session_event.connect(self._session_changed)
        self._bind_session()

    def _bind_session(self):
        from project_consumers import session_for
        session = session_for(self.root)
        if session is not self._session:
            if self._session is not None:
                self._session.remove_listener(self.session_event.emit)
            self._session = session
            if session is not None:
                session.add_listener(self.session_event.emit)

    @QtCore.Slot(object)
    def _session_changed(self, event):
        if self._refreshing:
            return
        if event.kind == "selection":
            from project_consumers import page_id_for_key
            selected = next((page.key for page in self.pages
                             if page_id_for_key(self.root, page.key) == self._session.selected_page_id), None)
            if selected is not None and selected != self.selected:
                self.selected = selected
                self.selection_changed.emit(selected)
        elif event.kind in {"project_replaced", "structure", "workspace"}:
            self.reload()

    def reload(self):
        if self._refreshing:
            return
        self._refreshing = True
        previous_error = self.compatibility_error
        try:
            self._bind_session()
            if self._session is not None:
                from project_consumers import project_legacy_views
                try:
                    if not self._session.is_restoring:
                        self._session.synchronize_workspace()
                    project_legacy_views(self._session)
                    self.compatibility_error = ""
                except (OSError, ValueError, RuntimeError) as error:
                    self.compatibility_error = str(error)
            pages = load_pages(self.directory)
        finally:
            self._refreshing = False
        if pages != self.pages:
            self.pages = pages
            if self.selected not in {page.key for page in pages}:
                self.selected = pages[0].key
            self.changed.emit(False)
            self.selection_changed.emit(self.selected)
        elif previous_error != self.compatibility_error:
            self.changed.emit(False)

    def update_pages(self, pages):
        self._bind_session()
        if self.compatibility_error:
            raise ValueError(self.compatibility_error)
        if pages == self.pages:
            return
        if self.change_guard is not None and not self.change_guard():
            raise ValueError("Wait for the current image to finish saving before changing pages.")
        previous = self.pages
        previous_model = self._session.project.to_dict() if self._session is not None else None
        self._refreshing = True
        try:
            save_pages(self.directory, pages)
            if self._session is not None:
                self._session.save()
                self._session._legacy_projection_signature = None
        except Exception:
            save_pages(self.directory, previous)
            if previous_model is not None:
                from project_model import ProjectModel
                self._session.adopt(ProjectModel.from_dict(previous_model), kind="workspace")
            raise
        finally:
            self._refreshing = False
        self.pages = pages
        if self.selected not in {page.key for page in pages}:
            self.selected = pages[-1].key
        self.changed.emit(True)
        self.selection_changed.emit(self.selected)

    def select(self, key):
        self._bind_session()
        if key in {page.key for page in self.pages}:
            self.selected = key
            if self._session is not None:
                from project_consumers import page_id_for_key
                self._session.select_page(page_id_for_key(self.root, key))
            self.selection_changed.emit(key)


_controllers = WeakValueDictionary()


def page_controller(root: str | Path) -> PageLayoutController:
    root = Path(root).resolve()
    key = str(root).casefold()
    controller = _controllers.get(key)
    if controller is None:
        controller = PageLayoutController(root)
        _controllers[key] = controller
    return controller


class PageControls(QtWidgets.QWidget):
    def __init__(self, controller: PageLayoutController, parent=None):
        super().__init__(parent)
        self.controller = controller
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        settings = QtWidgets.QHBoxLayout()
        settings.addWidget(QtWidgets.QLabel("Pages"))
        self.count = QtWidgets.QSpinBox()
        self.count.setRange(MIN_PAGES, MAX_PAGES)
        self.count.setKeyboardTracking(False)
        self.count.setAccessibleName("Number of pages")
        self.count.setToolTip("2–10 pages. Reducing the count preserves removed content for recovery.")
        settings.addWidget(self.count)
        settings.addWidget(QtWidgets.QLabel("Page type"))
        self.kind = QtWidgets.QComboBox()
        for definition in PAGE_TYPES.values():
            self.kind.addItem(definition.label, definition.artwork_template)
        self.kind.setAccessibleName("Selected page type")
        self.kind.setToolTip("All pages have artwork. Letter pages also have their own written message.")
        settings.addWidget(self.kind)
        self.move_left = QtWidgets.QPushButton("Move left")
        self.move_right = QtWidgets.QPushButton("Move right")
        settings.addWidget(self.move_left)
        settings.addWidget(self.move_right)
        settings.addStretch()
        layout.addLayout(settings)
        navigation = QtWidgets.QHBoxLayout()
        self.previous = QtWidgets.QPushButton("‹")
        self.previous.setAccessibleName("Previous page")
        self.next = QtWidgets.QPushButton("›")
        self.next.setAccessibleName("Next page")
        self.current = QtWidgets.QComboBox()
        self.current.setAccessibleName("Selected page")
        self.current.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        navigation.addWidget(self.previous)
        navigation.addWidget(self.current, 1)
        navigation.addWidget(self.next)
        layout.addLayout(navigation)
        self.count.valueChanged.connect(self._resize)
        self.kind.currentIndexChanged.connect(self._set_kind)
        self.current.currentIndexChanged.connect(self._select)
        self.previous.clicked.connect(lambda: self._step(-1))
        self.next.clicked.connect(lambda: self._step(1))
        self.move_left.clicked.connect(lambda: self._move(-1))
        self.move_right.clicked.connect(lambda: self._move(1))
        controller.changed.connect(self.refresh)
        controller.selection_changed.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        self.setEnabled(not bool(self.controller.compatibility_error))
        self.setToolTip(self.controller.compatibility_error)
        blockers = [QtCore.QSignalBlocker(widget) for widget in (self.count, self.current, self.kind)]
        pages = self.controller.pages
        labels = page_labels(pages)
        self.count.setValue(len(pages))
        self.current.clear()
        for index, page in enumerate(pages, 1):
            self.current.addItem(f"{labels[page.key]} · {index} of {len(pages)}", page.key)
        index = next((i for i, page in enumerate(pages) if page.key == self.controller.selected), 0)
        self.current.setCurrentIndex(index)
        page = pages[index]
        self.kind.setPlaceholderText(labels[page.key])
        self.kind.setCurrentIndex(self.kind.findData(page.kind))
        self.kind.setEnabled(bool(pages))
        self.previous.setEnabled(index > 0)
        self.next.setEnabled(index < len(pages) - 1)
        self.move_left.setEnabled(index > 0)
        self.move_right.setEnabled(index < len(pages) - 1)
        del blockers

    def _apply(self, pages):
        try:
            self.controller.update_pages(tuple(pages))
        except (OSError, ValueError, RuntimeError) as error:
            self.refresh()
            QtWidgets.QMessageBox.warning(self, "Pages could not be updated", str(error))

    def _resize(self, count):
        self._apply(resized_pages(self.controller.pages, count))

    def _select(self, index):
        if index >= 0:
            self.controller.select(self.current.itemData(index))

    def _step(self, offset):
        self.current.setCurrentIndex(self.current.currentIndex() + offset)

    def _set_kind(self, index):
        selected = self.current.currentIndex()
        if index < 0 or not 0 <= selected < len(self.controller.pages):
            return
        pages = list(self.controller.pages)
        pages[selected] = replace(pages[selected], kind=self.kind.itemData(index))
        self._apply(pages)

    def _move(self, offset):
        index = self.current.currentIndex()
        target = index + offset
        pages = list(self.controller.pages)
        if 0 <= index < len(pages) and 0 <= target < len(pages):
            pages[index], pages[target] = pages[target], pages[index]
            self._apply(pages)
