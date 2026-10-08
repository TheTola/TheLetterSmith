"""Shared page count, type, ordering, and horizontal navigation controls."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from weakref import WeakValueDictionary

from PySide6 import QtCore, QtWidgets

from page_layout import (
    MAX_PAGES, MIN_PAGES, load_pages, page_labels, resized_pages, save_pages,
)


class PageLayoutController(QtCore.QObject):
    changed = QtCore.Signal(bool)
    selection_changed = QtCore.Signal(str)

    def __init__(self, root: Path):
        super().__init__()
        self.directory = root / "gallery/user/pages"
        self.pages = load_pages(self.directory)
        self.selected = self.pages[0].key
        self.change_guard = None

    def reload(self):
        pages = load_pages(self.directory)
        if pages != self.pages:
            self.pages = pages
            if self.selected not in {page.key for page in pages}:
                self.selected = pages[0].key
            self.changed.emit(False)
            self.selection_changed.emit(self.selected)

    def update_pages(self, pages):
        if pages == self.pages:
            return
        if self.change_guard is not None and not self.change_guard():
            raise ValueError("Wait for the current image to finish saving before changing pages.")
        save_pages(self.directory, pages)
        self.pages = pages
        if self.selected not in {page.key for page in pages}:
            self.selected = pages[-1].key
        self.changed.emit(True)
        self.selection_changed.emit(self.selected)

    def select(self, key):
        if key in {page.key for page in self.pages}:
            self.selected = key
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
        self.count.setToolTip("2–10 pages, including Cover and End. Reducing the count keeps the images on disk.")
        settings.addWidget(self.count)
        settings.addWidget(QtWidgets.QLabel("Page type"))
        self.kind = QtWidgets.QComboBox()
        self.kind.addItem("Letter", "letter")
        self.kind.addItem("Wall", "wall")
        self.kind.setAccessibleName("Selected page type")
        self.kind.setToolTip("Letters show artwork. Each Wall also shows the shared written message.")
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
        self.kind.setEnabled(0 < index < len(pages) - 1)
        self.previous.setEnabled(index > 0)
        self.next.setEnabled(index < len(pages) - 1)
        self.move_left.setEnabled(1 < index < len(pages) - 1)
        self.move_right.setEnabled(0 < index < len(pages) - 2)
        del blockers

    def _apply(self, pages):
        try:
            self.controller.update_pages(tuple(pages))
        except (OSError, ValueError) as error:
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
        if index < 0 or not 0 < selected < len(self.controller.pages) - 1:
            return
        pages = list(self.controller.pages)
        pages[selected] = replace(pages[selected], kind=self.kind.itemData(index))
        self._apply(pages)

    def _move(self, offset):
        index = self.current.currentIndex()
        target = index + offset
        pages = list(self.controller.pages)
        if 0 < index < len(pages) - 1 and 0 < target < len(pages) - 1:
            pages[index], pages[target] = pages[target], pages[index]
            self._apply(pages)
