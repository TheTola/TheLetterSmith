"""Explicit, cancellable setup for the optional offline writing model."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import zipfile

from PySide6 import QtCore, QtWidgets

from dictation_models import digest, download
from language_runtime import Cancelled, MODEL_HASHES, runtime_hashes, verify_pack
from project_paths import runtime_application_paths

_REVISION = "1cd86afb9a95c410a6038ab3b40d8b578c892266"
_RUNTIME_URL = "https://github.com/ggml-org/llama.cpp/releases/download/b11969/llama-b11969-bin-win-vulkan-x64.zip"
_RUNTIME_HASH = "bc0c860370f38958fa62b487ed012eeb326f31135acffed61adbc08da0b537fa"


class ModelSetupWorker(QtCore.QThread):
    status = QtCore.Signal(str)
    progress = QtCore.Signal(object, object)
    completed = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, path, *, install=False, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.install = install
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def run(self):
        try:
            if self.install:
                self.path.mkdir(parents=True, exist_ok=True)
                assets = {name: (f"https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/resolve/{_REVISION}/{name}", value)
                          for name, value in MODEL_HASHES.items()}
                assets["runtime.zip"] = (_RUNTIME_URL, _RUNTIME_HASH)
                for name, (url, expected) in assets.items():
                    self.status.emit("Downloading the local writing model…")
                    download(self.path / name, url, expected, self.cancelled.is_set,
                             self.progress.emit, max_bytes=4 * 1024**3)
                self.status.emit("Preparing the writing engine…")
                trusted = runtime_hashes()
                with tempfile.TemporaryDirectory(prefix="writing-runtime-", dir=self.path) as temporary:
                    staging = Path(temporary)
                    with zipfile.ZipFile(self.path / "runtime.zip") as archive:
                        if sum(info.file_size for info in archive.infolist()) > 2 * 1024**3:
                            raise ValueError("The writing engine archive is too large.")
                        for name, expected in trusted.items():
                            if self.cancelled.is_set():
                                raise InterruptedError()
                            info = archive.getinfo(name)
                            if info.is_dir() or info.file_size > 512 * 1024**2:
                                raise ValueError("Invalid writing engine archive.")
                            target = staging / name
                            with archive.open(info) as source, target.open("wb") as destination:
                                while block := source.read(1024**2):
                                    if self.cancelled.is_set():
                                        raise InterruptedError()
                                    destination.write(block)
                            if digest(target, self.cancelled.is_set) != expected:
                                raise ValueError("Writing engine verification failed.")
                    runtime = self.path / "runtime"
                    runtime.mkdir(exist_ok=True)
                    for name in trusted:
                        os.replace(staging / name, runtime / name)
            self.status.emit("Verifying the local writing model…")
            verify_pack(self.path, self.cancelled.is_set)
            if self.cancelled.is_set():
                raise InterruptedError()
            self.completed.emit(str(self.path))
        except (InterruptedError, Cancelled):
            self.failed.emit("Setup canceled. Partial downloads can be resumed.")
        except Exception:
            self.failed.emit("The writing model could not be verified or installed. Existing text was kept.")


def open_setup(parent=None, *, service=None):
    from language_ai import get_grammar_service, installed_pack
    from ui_dialogs import LetterSmithDialog
    service = service or get_grammar_service()
    if service._dialog is not None:
        service._dialog.show()
        service._dialog.raise_()
        service._dialog.activateWindow()
        return service._dialog

    class SetupDialog(LetterSmithDialog):
        def __init__(self):
            super().__init__(parent, title="AI writing settings", width=490)
            self.worker = None
            self.closing = False
            self.status = QtWidgets.QLabel(self.panel)
            self.status.setWordWrap(True)
            self.status.setTextFormat(QtCore.Qt.PlainText)
            self.content_layout.addWidget(self.status)
            self.progress = QtWidgets.QProgressBar(self.panel)
            self.progress.hide()
            self.content_layout.addWidget(self.progress)
            row = QtWidgets.QHBoxLayout()
            self.download_button = QtWidgets.QPushButton("Download model", self.panel)
            self.download_button.clicked.connect(self.download_model)
            row.addWidget(self.download_button)
            self.choose_button = QtWidgets.QPushButton("Use existing model…", self.panel)
            self.choose_button.clicked.connect(self.choose_model)
            row.addWidget(self.choose_button)
            self.content_layout.addLayout(row)
            self.done = QtWidgets.QPushButton("Done", self.panel)
            self.done.clicked.connect(self.accept)
            self.content_layout.addWidget(self.done)
            self.refresh()
            QtWidgets.QApplication.instance().aboutToQuit.connect(self.shutdown)

        def refresh(self):
            self.status.setText("Local writing model found. It is verified before use. Corrections run on this computer."
                                if installed_pack(service.store) else
                                "Download the local writing model once (about 3.1 GB), or use an existing verified pack. "
                                "Your text stays on this computer.")

        def begin(self, path, install):
            if self.worker is not None:
                return
            worker = self.worker = ModelSetupWorker(path, install=install, parent=self)
            self.download_button.setEnabled(False)
            self.choose_button.setEnabled(False)
            self.done.setText("Cancel")
            self.done.clicked.disconnect()
            self.done.clicked.connect(self.reject)
            self.progress.setRange(0, 0)
            self.progress.show()
            worker.status.connect(self.status.setText)
            worker.progress.connect(self.show_progress)
            worker.completed.connect(self.installed)
            worker.failed.connect(self.status.setText)
            worker.finished.connect(self.finished_work)
            worker.start()

        def show_progress(self, received, total):
            if total:
                self.progress.setRange(0, 100)
                self.progress.setValue(min(100, int(received * 100 / total)))

        def download_model(self):
            path = runtime_application_paths().app_data_root / "offline_models" / "writing"
            self.begin(path, True)

        def choose_model(self):
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "Local writing model folder")
            if path:
                self.begin(path, False)

        def installed(self, path):
            if not self.closing:
                try:
                    service.store.set_pack(path)
                    service.configuration_updated()
                    self.refresh()
                except OSError:
                    self.status.setText("The verified model is ready, but its setting could not be saved.")

        def finished_work(self):
            worker, self.worker = self.worker, None
            worker.deleteLater()
            self.progress.hide()
            self.download_button.setEnabled(True)
            self.choose_button.setEnabled(True)
            self.done.setEnabled(True)
            self.done.setText("Done")
            self.done.clicked.disconnect()
            self.done.clicked.connect(self.accept)
            if self.closing:
                super().reject()

        def reject(self):
            if self.worker is not None:
                self.closing = True
                self.worker.cancel()
                self.status.setText("Canceling…")
                self.done.setEnabled(False)
            else:
                super().reject()

        def closeEvent(self, event):
            if self.worker is not None:
                event.ignore()
                self.reject()
            else:
                super().closeEvent(event)

        def shutdown(self):
            if self.worker is not None:
                self.closing = True
                self.worker.cancel()
                self.worker.wait()

    dialog = service._dialog = SetupDialog()
    def finished(*_):
        service._dialog = None
        dialog.deleteLater()
    dialog.finished.connect(finished)
    dialog.show()
    return dialog
