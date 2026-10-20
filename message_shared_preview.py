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
    def __init__(self, revision, session, target, adapter, cache, *, external=False):
        super().__init__()
        self.revision, self.session, self.target = revision, session, target
        self.adapter, self.cache = adapter, cache
        self.cancelled = Event()
        self.external = external
        self.signals = _BuildSignals()

    def run(self):
        temporary = render = None
        warnings = ()
        error = ""
        try:
            if self.cancelled.is_set() or not self.session.target_is_current(self.target):
                return
            render = self.adapter.capture_render(self.session, output_mode="preview")
            issues = self.adapter.validate_render(render)
            if self.external and any(issue.code in {"missing_artwork", "invalid_artwork", "message_resource", "missing_sound"} for issue in issues):
                raise ValueError("A changed resource is missing or incomplete. Repair it or relink it in its editor.")
            warnings = tuple(issue.message for issue in issues
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
    expand_requested = QtCore.Signal()
    contrast_changed = QtCore.Signal(float)

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

        self._page_aspect = 2 / 3
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
        QtWidgets.QApplication.instance().installEventFilter(self)
        self.view.hide()
        self.view.loadFinished.connect(self._loaded)
        self._pool = QtCore.QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._tasks = {}
        self._revision = 0
        self._displayed_revision = None
        self._page_id = None
        self._current = None
        self._retired = []
        self._view_released = True
        self._unload_pending = False
        self._closed = False
        self._request_key = None
        self._resource_stamp = None
        self._pending_resource_stamp = None
        self._warning = ""
        self.viewed_page_id = None
        self._poll = QtCore.QTimer(self)
        self._poll.setInterval(400)
        self._poll.timeout.connect(self._inspect_page)
        self._resource_timer = QtCore.QTimer(self)
        self._resource_timer.setInterval(1000)
        self._resource_timer.timeout.connect(self.check_resources)
        self._cleanup_timer = QtCore.QTimer(self)
        self._cleanup_timer.setSingleShot(True)
        self._cleanup_timer.setInterval(250)
        self._cleanup_timer.timeout.connect(self._cleanup_retired)

    def request(self, page_id, *, force=False, external=False):
        if self._closed:
            return
        key = (self.session.session_id, page_id, self.adapter.render_content_key(self.session.project.render_snapshot()))
        if not force and key == self._request_key:
            return
        keep = (self._current is not None and self._page_id == page_id
                and self._current[1].session_id == self.session.session_id)
        if keep:
            self._revision += 1
            for task in self._tasks.values():
                task.cancelled.set()
            self._warning = "Preview out of date — updating."
        else:
            self.invalidate()
        self._request_key = key
        self._page_id = page_id
        target = self.session.capture_target(page_id, fields=("type", "message", "artwork", "design_overrides"), include_shared=False)
        task = _Build(self._revision, self.session, target, self.adapter, self.cache, external=external)
        self._tasks[self._revision] = task
        task.signals.finished.connect(self._finished)
        self.status_changed.emit("Updating shared preview…")
        self._pool.start(task)

    def invalidate(self):
        self._revision += 1
        self._displayed_revision = None
        self._poll.stop()
        self._resource_timer.stop()
        self._request_key = None
        self._resource_stamp = self._pending_resource_stamp = None
        self._warning = ""
        self.viewed_page_id = None
        self._page_id = None
        for task in self._tasks.values():
            task.cancelled.set()
        if self._current is not None:
            self._retired.append(self._current[0])
            self._current = None
        self.view.hide()
        if self._unload_pending:
            return
        if self._view_released and self.view.url().toString() in ("", "about:blank"):
            self._cleanup_retired()
            return
        self._view_released = False
        self._unload_pending = True
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
                self._warning = ("Preview out of date: " if self._current else "Preview unavailable: ") + error
                self.status_changed.emit(self._warning)
                self._request_key = None
            return
        if self._current is not None:
            self._retired.append(self._current[0])
        self._current = (temporary, render, warnings)
        self._displayed_revision = None
        self._poll.stop()
        self._warning = ""
        self._resource_stamp = self.adapter.referenced_resource_stamp(render)
        self._view_released = False
        self._unload_pending = False
        self.view.setUrl(QtCore.QUrl.fromLocalFile(str(Path(temporary.name) / "index.html")))

    def _loaded(self, ok):
        if self.view.url().toString() == "about:blank":
            if ok:
                self._view_released = True
                self._unload_pending = False
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
            self._displayed_revision = revision
            self._poll.start()
            self._resource_timer.start()
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
        if self._displayed_revision != revision:
            return

        def inspected(state):
            if (self._closed or revision != self._revision or self._current is None
                    or self._displayed_revision != revision):
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
            contrast = state.get("contrast")
            if page_id == self._page_id and not self._warning and isinstance(contrast, (int, float)):
                self.contrast_changed.emit(float(contrast))
            _, render, warnings = self._current
            page = next(((i, p) for i, p in enumerate(render.snapshot["pages"], 1) if p["id"] == page_id), None)
            if page is not None:
                i, data = page
                note = f"Preview — Page {i} — {data.get('name') or 'Letter'}. Sound muted."
                if page_id != self._page_id:
                    note += " Editor selection is unchanged."
                if warnings:
                    note += " " + " ".join(warnings)
                if self._warning:
                    note += " " + self._warning
                for issue in state.get("issues", []):
                    if isinstance(issue, dict) and issue.get("page_id") == page_id and isinstance(issue.get("message"), str):
                        note += " " + issue["message"]
                self.status_changed.emit(note)

        self.view.page().runJavaScript(
            "JSON.stringify({page_id: window.LetterSmithViewer?.currentPageId() || null, "
            "contrast: (() => { const raw=document.querySelector('.text-wall.is-open')?.dataset.minimumContrast; "
            "return raw ? Number(raw) : null; })(), "
            "issues: window.LetterSmithViewer?.layoutIssues?.() || []})", inspected,
        )

    def check_resources(self):
        if self._closed or self._current is None or self._tasks or not self.isVisible():
            return
        if self._current[1].session_id != self.session.session_id:
            self._resource_timer.stop()
            self._poll.stop()
            return
        stamp = self.adapter.referenced_resource_stamp(self._current[1])
        if stamp == self._resource_stamp:
            self._pending_resource_stamp = None
            return
        # Two stable observations avoid importing a file halfway through a write.
        if stamp != self._pending_resource_stamp:
            self._pending_resource_stamp = stamp
            self._warning = "Preview out of date — checking changed resources."
            self.status_changed.emit(self._warning)
            return
        self._resource_stamp = stamp
        self._pending_resource_stamp = None
        self.request(self._page_id, force=True, external=True)

    def set_zoom(self, value):
        self.view.setZoomFactor(max(0.25, min(2.0, value / 100)))

    def set_page_aspect(self, width, height):
        if width > 0 and height > 0:
            self._page_aspect = width / height
            self._fit_view()

    def _fit_view(self):
        width = min(self.width(), round(self.height() * self._page_aspect))
        height = min(self.height(), round(width / self._page_aspect))
        self.view.setGeometry((self.width() - width) // 2,
                              (self.height() - height) // 2, width, height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_view()

    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.MouseButtonDblClick and self.isVisible():
            widget = watched if isinstance(watched, QtWidgets.QWidget) else None
            if widget is not None and (widget is self or self.isAncestorOf(widget)):
                self.expand_requested.emit()
                return True
        return False

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
        elapsed = QtCore.QElapsedTimer()
        elapsed.start()
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
            # Observe invalidate's one navigation. A second about:blank load
            # cancels the first and can lose its completion on a cold renderer.
            self.invalidate()
            if not self._pool.waitForDone(timeout_ms):
                return False
            unloaded = self._view_released and not self._unload_pending
            remaining = max(0, timeout_ms - elapsed.elapsed())
            if not unloaded and remaining:
                timer.start(remaining)
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
        QtWidgets.QApplication.instance().removeEventFilter(self)
        self._cleanup_timer.stop()
        self._cleanup_retired()
        if self._retired:
            _LOGGER.warning("Temporary Messages preview files remain in %s", self.cache)
        return True
