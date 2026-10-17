"""Messages view of the Forge-owned renderer, without another composition path."""
from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
import tempfile
from threading import Event

from PySide6 import QtCore, QtGui, QtWidgets


_LOGGER = logging.getLogger(__name__)


def create_shared_preview(parent, session, cache):
    try:
        adapter = importlib.import_module("forge_render")
    except ModuleNotFoundError as error:
        if error.name == "forge_render":
            return None
        raise
    return SharedMessagePreview(parent, session, cache, adapter)


class _BuildSignals(QtCore.QObject):
    finished = QtCore.Signal(int, object)


class _Build(QtCore.QRunnable):
    def __init__(self, revision, session, target, adapter, cache):
        super().__init__()
        self.revision, self.session, self.target = revision, session, target
        self.adapter, self.cache = adapter, cache
        self.cancelled = Event()
        self.signals = _BuildSignals()

    def run(self):
        temporary = render = None
        warnings = ()
        error = ""
        try:
            if self.cancelled.is_set() or not self.session.target_is_current(self.target):
                return
            render = self.adapter.capture_render(self.session, output_mode="preview")
            warnings = tuple(issue.message for issue in self.adapter.validate_render(render)
                             if not issue.blocks_preview)
            temporary = tempfile.TemporaryDirectory(prefix="generation-", dir=self.cache)
            self.adapter.build_render_to(render, temporary.name, seed_sfx=False,
                                         cancelled=self.cancelled.is_set)
            if not self.adapter.render_is_current(self.session, render):
                raise self.adapter.StaleRenderError("The project changed while its preview was built.")
        except Exception as failure:
            error = str(failure)
        finally:
            self.signals.finished.emit(self.revision, (temporary, render, warnings, error))


class SharedMessagePreview(QtWidgets.QWidget):
    """Own only preview jobs, temporary outputs and a local, muted browser view."""

    status_changed = QtCore.Signal(str)

    def __init__(self, parent, session, cache, adapter):
        super().__init__(parent)
        from PySide6.QtWebEngineCore import (
            QWebEnginePage, QWebEngineProfile, QWebEngineSettings,
            QWebEngineUrlRequestInterceptor,
        )
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self.session, self.adapter = session, adapter
        self.cache = Path(cache).resolve()
        self.cache.mkdir(parents=True, exist_ok=True)
        allowed_root = self.cache

        class LocalRequests(QWebEngineUrlRequestInterceptor):
            def interceptRequest(self, info):
                url = info.requestUrl()
                allowed = url.scheme() in ("data", "blob") or url.toString() == "about:blank"
                if url.isLocalFile():
                    try:
                        allowed = Path(url.toLocalFile()).resolve().is_relative_to(allowed_root)
                    except (OSError, ValueError):
                        allowed = False
                info.block(not allowed)

        class PreviewPage(QWebEnginePage):
            def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
                if url.toString() == "about:blank":
                    return True
                if not url.isLocalFile():
                    return False
                try:
                    return Path(url.toLocalFile()).resolve().is_relative_to(allowed_root)
                except (OSError, ValueError):
                    return False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # Create the view before its profile so child destruction releases the
        # page before destroying the profile.
        self.view = QWebEngineView(self)
        self.profile = QWebEngineProfile(self)
        self.interceptor = LocalRequests(self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.view.setPage(PreviewPage(self.profile, self.view))
        self.view.page().setAudioMuted(True)
        self.view.page().setBackgroundColor(QtGui.QColor("#161b22"))
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, False)
        self.view.setAccessibleName("Interactive preview of the selected Letter")
        layout.addWidget(self.view)
        self.view.hide()
        self.view.loadFinished.connect(self._loaded)
        self._pool = QtCore.QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._tasks = {}
        self._revision = 0
        self._page_id = None
        self._current = None
        self._retired = []
        self._view_released = True
        self._closed = False
        self.viewed_page_id = None
        self._poll = QtCore.QTimer(self)
        self._poll.setInterval(400)
        self._poll.timeout.connect(self._inspect_page)
        self._cleanup_timer = QtCore.QTimer(self)
        self._cleanup_timer.setSingleShot(True)
        self._cleanup_timer.setInterval(250)
        self._cleanup_timer.timeout.connect(self._cleanup_retired)

    def request(self, page_id):
        if self._closed:
            return
        self.invalidate()
        self._page_id = page_id
        target = self.session.capture_target(page_id, fields=("type", "message", "artwork", "design_overrides"))
        task = _Build(self._revision, self.session, target, self.adapter, self.cache)
        self._tasks[self._revision] = task
        task.signals.finished.connect(self._finished)
        self.status_changed.emit("Updating shared preview…")
        self._pool.start(task)

    def invalidate(self):
        self._revision += 1
        self._poll.stop()
        self.viewed_page_id = None
        self._page_id = None
        for task in self._tasks.values():
            task.cancelled.set()
        if self._current is not None:
            self._retired.append(self._current[0])
            self._current = None
        self._view_released = False
        self.view.hide()
        self.view.stop()
        self.view.setUrl(QtCore.QUrl("about:blank"))

    def _finished(self, revision, result):
        task = self._tasks.pop(revision, None)
        temporary, render, warnings, error = result
        current = (task is not None and not self._closed and revision == self._revision
                   and not task.cancelled.is_set() and self.session.target_is_current(task.target))
        if current and render is not None:
            current = self.adapter.render_is_current(self.session, render, check_sources=False)
        if not current or error or temporary is None:
            if temporary is not None:
                self._retired.append(temporary)
                self._cleanup_retired()
            if current and error:
                self.status_changed.emit("Preview unavailable: " + error)
            return
        self._current = (temporary, render, warnings)
        self._view_released = False
        self.view.setUrl(QtCore.QUrl.fromLocalFile(str(Path(temporary.name) / "index.html")))

    def _loaded(self, ok):
        if self.view.url().toString() == "about:blank":
            if ok:
                self._view_released = True
                self._cleanup_retired()
            return
        if self._closed or self._current is None:
            return
        temporary, render, _ = self._current
        if Path(self.view.url().toLocalFile()).resolve() != Path(temporary.name) / "index.html":
            return
        if not ok:
            self.status_changed.emit("Shared preview could not be opened.")
            return
        self._view_released = True
        if not self.adapter.render_is_current(self.session, render, check_sources=False):
            self.invalidate()
            return
        self._cleanup_retired()
        revision = self._revision
        page_id = self._page_id

        def selected(success):
            if self._closed or revision != self._revision:
                return
            if not success:
                self.status_changed.emit("The shared viewer could not open this Letter page.")
                return
            self.view.show()
            self._poll.start()
            self._inspect_page()

        # Sound's shared playback gate rejects hidden documents. Show the view
        # before requesting its start action so GIFs also start in this preview.
        self.view.show()
        # Use Forge's public page-ID API and the viewer's existing start action.
        # Composition, layout, animation and scrolling stay entirely in Forge.
        self.view.page().runJavaScript(
            "(() => { const viewer = window.LetterSmithViewer; "
            f"if (!viewer || !viewer.goToPage({json.dumps(page_id)})) return false; "
            "document.getElementById('curtain-overlay')?.click(); return true; })()",
            selected,
        )

    def _inspect_page(self):
        revision = self._revision

        def inspected(state):
            if self._closed or revision != self._revision or self._current is None:
                return
            try:
                state = json.loads(state)
            except (TypeError, ValueError):
                return
            if not isinstance(state, dict):
                return
            page_id = state.get("page_id")
            if not isinstance(page_id, str):
                return
            self.viewed_page_id = page_id
            _, render, warnings = self._current
            page = next(((i, p) for i, p in enumerate(render.snapshot["pages"], 1) if p["id"] == page_id), None)
            if page is not None:
                i, data = page
                note = f"Preview — Page {i} — {data.get('name') or 'Letter'}. Sound muted."
                if page_id != self._page_id:
                    note += " Editor selection is unchanged."
                if warnings:
                    note += " " + " ".join(warnings)
                for issue in state.get("issues", []):
                    if isinstance(issue, dict) and issue.get("page_id") == page_id and isinstance(issue.get("message"), str):
                        note += " " + issue["message"]
                self.status_changed.emit(note)

        self.view.page().runJavaScript(
            "JSON.stringify({page_id: window.LetterSmithViewer?.currentPageId() || null, "
            "issues: window.LetterSmithViewer?.layoutIssues?.() || []})", inspected,
        )

    def set_zoom(self, value):
        self.view.setZoomFactor(max(0.25, min(2.0, value / 100)))

    def _cleanup_retired(self):
        if not self._view_released:
            return
        pending = []
        for temporary in self._retired:
            try:
                temporary.cleanup()
            except OSError:
                pending.append(temporary)
        self._retired = pending
        if pending and not self._closed:
            self._cleanup_timer.start()

    def release(self, timeout_ms=5000):
        """Cancel builds and confirm browser unload before project restoration."""
        self.invalidate()
        if not self._pool.waitForDone(timeout_ms):
            return False
        loop = QtCore.QEventLoop()
        timer = QtCore.QTimer()
        timer.setSingleShot(True)
        unloaded = False

        def blank_loaded(ok):
            nonlocal unloaded
            if ok and self.view.url().toString() == "about:blank":
                unloaded = True
                loop.quit()

        self.view.loadFinished.connect(blank_loaded)
        timer.timeout.connect(loop.quit)
        try:
            self.view.setUrl(QtCore.QUrl("about:blank"))
            timer.start(min(timeout_ms, 1500))
            loop.exec(QtCore.QEventLoop.ExcludeUserInputEvents)
        finally:
            timer.stop()
            self.view.loadFinished.disconnect(blank_loaded)
        return unloaded

    def shutdown(self):
        if self._closed:
            return True
        if not self.release():
            return False
        self._closed = True
        self._cleanup_timer.stop()
        self._cleanup_retired()
        if self._retired:
            _LOGGER.warning("Temporary Messages preview files remain in %s", self.cache)
        return True
