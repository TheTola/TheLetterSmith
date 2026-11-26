"""Page-bound Messages UI; authored documents remain in ProjectSession."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from PySide6 import QtCore, QtGui, QtWidgets

from Editor import Editor
from letter_page import LETTER_PAGE_PRESET_LABELS, normalized_letter_page_settings
from message_document_import import message_resources, stage_import_html
from message_format import message_plain_text
from message_history import list_revisions, read_revision, record_draft
from message_html import sanitize_message_html
from message_page_tools import recipient_name_edits, replace_text_ranges
from project_paths import application_paths
from ui_dialogs import LetterSmithConfirmationDialog, LetterSmithDialog


class ReadabilityControls(QtWidgets.QWidget):
    """Synchronized views of the selected page's existing styling controls."""

    def __init__(self, workspace, parent=None, *, editor=False):
        super().__init__(parent)
        self.workspace = workspace
        self.setObjectName("letterReadabilityControls")
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setStyleSheet(
            "#letterReadabilityControls QComboBox, #letterReadabilityControls QSpinBox"
            "{min-height:24px;padding:1px 4px;font:9pt 'Segoe UI';}"
            "#letterReadabilityControls QSpinBox{padding-right:22px;}"
            "#letterReadabilityControls QSpinBox::up-button,"
            "#letterReadabilityControls QSpinBox::down-button{width:20px;}"
            "#letterReadabilityControls QLabel{font:9pt 'Segoe UI';}"
        )
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)
        self.preset = QtWidgets.QComboBox(self)
        self.preset.setAccessibleName("Letter paper")
        self.preset.setToolTip("Paper for the selected letter")
        for key, label in LETTER_PAGE_PRESET_LABELS.items():
            self.preset.addItem(label, key)
        row.addWidget(self.preset)
        self.opacity = QtWidgets.QSpinBox(self)
        self.opacity.setRange(0, 100)
        self.opacity.setSuffix("%")
        self.opacity.setAccessibleName("Paper opacity")
        self.opacity.setToolTip("Paper opacity: 0% reveals the artwork")
        self.opacity.setKeyboardTracking(False)
        self.opacity.setVisible(not editor)
        if not editor:
            row.addWidget(self.opacity)
        self.color = QtWidgets.QToolButton(self)
        self.color.setText("Normal Text Color")
        self.color.setAccessibleName("Normal Text Color")
        self.color.clicked.connect(self.choose_color)
        self.color.setVisible(editor)
        self.contrast = QtWidgets.QLabel(self)
        self.contrast.setAccessibleName("Letter contrast")
        self.contrast.setVisible(not editor)
        self.suggestion = None
        self.suggest_button = None
        if not editor:
            self.suggest_button = QtWidgets.QToolButton(self)
            self.suggest_button.hide()
            row.addWidget(self.contrast)
            row.addWidget(self.suggest_button)
            self.suggest_button.clicked.connect(self.apply_suggestion)
        row.addStretch(1)
        self.zoom = QtWidgets.QSpinBox(self)
        self.zoom.setRange(25, 200)
        self.zoom.setSuffix("%")
        self.zoom.setKeyboardTracking(False)
        self.zoom.setMinimumWidth(90)
        self.zoom.setAccessibleName("Page preview zoom")
        self.zoom.setToolTip("Preview zoom: type 25–200% or use the arrows")
        row.addWidget(self.zoom)
        if editor:
            row.addWidget(self.color)
        self.preset.currentIndexChanged.connect(self.apply)
        self.opacity.valueChanged.connect(self.apply)
        self.zoom.valueChanged.connect(workspace.zoom.setValue)
        workspace.readability_changed.connect(self.sync)
        self.sync()

    def apply_theme_assets(self, service):
        service.apply_semantic_styles(self)

    def apply(self, *_):
        workspace = self.workspace
        if workspace.binding is None or workspace._failed:
            return
        # Transparent displays 0% without replacing the last paper opacity.
        patch = ({"message_overlay_opacity": self.opacity.value()}
                 if self.sender() is self.opacity else
                 {"message_overlay_preset": self.preset.currentData()})
        workspace.set_readability(patch)

    def choose_color(self):
        editor = self.workspace.editor
        if editor is None:
            return
        page_id = self.workspace.page_id
        key = "normal_text"
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(editor.named_styles.active_set.get(key).font_color), self,
            f"Normal Text Color · {self.preset.currentText()}")
        if self.workspace.editor is editor and self.workspace.page_id == page_id:
            editor.set_paper_text_color(key, color)
            self.workspace.readability_changed.emit()

    def apply_suggestion(self):
        if self.suggestion is not None:
            preset, opacity, _ratio = self.suggestion
            self.workspace.set_readability({"message_overlay_preset": preset, "message_overlay_opacity": opacity})

    def sync(self, *_):
        workspace = self.workspace
        self.setEnabled(workspace.page_id is not None and not workspace._failed)
        if workspace.page_id is None:
            self.contrast.clear()
            if self.suggest_button is not None:
                self.suggest_button.hide()
            return
        preset, opacity, _, _ = normalized_letter_page_settings(
            workspace._display_settings())
        with QtCore.QSignalBlocker(self.preset), QtCore.QSignalBlocker(self.opacity), QtCore.QSignalBlocker(self.zoom):
            self.preset.setCurrentIndex(self.preset.findData(preset))
            if self.opacity.value() != opacity:
                self.opacity.setValue(opacity)
            self.opacity.setEnabled(preset != "clear")
            # setValue also replaces uncommitted spin-box text when its numeric
            # value is unchanged. Preview/contrast refreshes must not erase typing.
            if self.zoom.value() != workspace.zoom.value():
                self.zoom.setValue(workspace.zoom.value())
        self.setToolTip(workspace.binding.label)
        if workspace.editor is not None:
            editor = workspace.editor
            color = editor.named_styles.active_set.get("normal_text").font_color
            self.color.setStyleSheet(f"QToolButton{{border-bottom:3px solid {color};padding:3px 6px;}}")
            self.color.setToolTip(f"Normal Text Color: {color}")
            self.contrast.setText(editor.readability_indicator.text())
            self.contrast.setToolTip(editor.readability_indicator.toolTip())
            if workspace._preview_contrast is not None:
                ratio = workspace._preview_contrast
                self.contrast.setText("Contrast good" if ratio >= 4.5 else "Contrast reduced" if ratio >= 3 else "Low contrast")
                self.contrast.setToolTip(f"Lowest sampled contrast in the visible page: {ratio:.1f}:1.")
            if self.suggest_button is not None:
                self.suggestion = editor.suggest_paper(workspace._preview_contrast)
                self.suggest_button.setVisible(self.suggestion is not None)
                if self.suggestion is not None:
                    paper, percent, ratio = self.suggestion
                    self.suggest_button.setText(f"Try {LETTER_PAGE_PRESET_LABELS[paper]} · {percent}%")
                    self.suggest_button.setToolTip(f"Apply suggested paper. Estimated contrast {ratio:.1f}:1.")


@dataclass
class _Binding:
    target: object
    document: dict
    label: str


@dataclass(frozen=True)
class _ReviewPage:
    target: object
    document: dict
    label: str
    text: str


class MessagesWorkspace(QtWidgets.QWidget):
    """One capable editor, selected by permanent page ID, with transient view state."""

    session_changed = QtCore.Signal(object)
    background_requested = QtCore.Signal(str)
    images_requested = QtCore.Signal()
    readability_changed = QtCore.Signal()

    def __init__(self, owner, session):
        super().__init__(owner)
        self.owner = owner
        self.session = session
        self.project_root = owner.project_root
        self.project_state = owner.project_state
        self.project_save_service = owner.project_save_service
        self.editor: Editor | None = None
        self.binding: _Binding | None = None
        self._last_letter = None
        self._seen_session = session.session_id
        self._views = {}
        self._recipient_change = None
        self._pending_drafts = {}
        self._committing = False
        self._refreshing = False
        self._dirty = False
        self._failed = False
        self._closed = False
        self._pending_import = None
        self._review_import = None
        self._pixmap = QtGui.QPixmap()
        self.preview_page_id = None
        self.preview_background = None
        self.preview_html = ""
        self._preview_contrast = None
        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(650)
        self._save_timer.timeout.connect(self.save)
        self._preview_timer = QtCore.QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(250)
        self._preview_timer.timeout.connect(self.render_preview)
        # Reuse the existing Messages renderer and cancellation gate.
        from Message_tab import _MessageRenderGate
        self._render_gate = _MessageRenderGate()
        self._render_pool = QtCore.QThreadPool(self)
        self._render_pool.setMaxThreadCount(1)
        self._render_tasks = {}
        paths = application_paths(self.project_root)
        self._history_root = paths.recovery_root / "message-drafts"
        cache = paths.cache_root / "messages"
        cache.mkdir(parents=True, exist_ok=True)
        self._preview_path = cache / f"{uuid4().hex}.png"

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QtWidgets.QHBoxLayout()
        self.selector = QtWidgets.QComboBox()
        self.selector.setAccessibleName("Letter page to edit")
        self.selector.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.selector.setMinimumContentsLength(18)
        self.selector.setMaximumWidth(320)
        header.addWidget(self.selector)
        header.addStretch(1)
        self.preview_device = QtWidgets.QComboBox(self)
        self.preview_device.setAccessibleName("Preview device")
        self.preview_device.addItem("Page View", None)
        self.preview_device.addItem("Phone View", (390, 844))
        self.preview_device.addItem("Desktop View", (1440, 900))
        header.addWidget(self.preview_device)
        self.preview_toggle = QtWidgets.QPushButton("Preview")
        self.preview_toggle.setCheckable(True)
        self.preview_toggle.setChecked(True)
        header.addWidget(self.preview_toggle)
        layout.addLayout(header)
        self.heading = QtWidgets.QLabel()
        self.heading.setWordWrap(True)
        self.heading.setAccessibleName("Currently edited Letter page")
        layout.addWidget(self.heading)
        self.notice = QtWidgets.QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.actions = QtWidgets.QWidget()
        actions = QtWidgets.QHBoxLayout(self.actions)
        actions.setContentsMargins(0, 0, 0, 0)
        self.save_button = QtWidgets.QPushButton("Save Draft")
        self.save_button.clicked.connect(self.save)
        self.review_button = QtWidgets.QPushButton("Review Import")
        self.review_button.clicked.connect(self.review_import)
        self.review_button.hide()
        self.recover_button = QtWidgets.QPushButton("Discard draft and reload")
        self.recover_button.clicked.connect(self.discard_draft)
        self.recover_button.hide()
        self.update_name_button = QtWidgets.QPushButton("Update Name in Messages")
        self.update_name_button.clicked.connect(self.update_name)
        self.copy_style_button = QtWidgets.QToolButton()
        self.copy_style_button.setText("Copy Style From")
        self.copy_style_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.copy_style_menu = QtWidgets.QMenu(self.copy_style_button)
        self.copy_style_menu.aboutToShow.connect(self.populate_style_sources)
        self.copy_style_button.setMenu(self.copy_style_menu)
        self.proofread_letter_button = QtWidgets.QPushButton("Proofread Letter")
        self.proofread_letter_button.clicked.connect(self.proofread_letter)
        self.history_button = QtWidgets.QPushButton("Draft History")
        self.history_button.clicked.connect(self.open_draft_history)
        for widget in (self.save_button, self.recover_button, self.update_name_button,
                       self.copy_style_button, self.proofread_letter_button, self.history_button):
            actions.addWidget(widget)
        actions.addStretch(1)
        layout.addWidget(self.actions)
        # A delayed import can outlive every Letter page in the active project.
        layout.addWidget(self.review_button, 0, QtCore.Qt.AlignLeft)

        self.empty = QtWidgets.QWidget()
        empty_layout = QtWidgets.QVBoxLayout(self.empty)
        empty_layout.addWidget(QtWidgets.QLabel("No Letter pages in this project"))
        self.add_button = QtWidgets.QPushButton("Add Letter Page")
        self.add_button.clicked.connect(self.add_letter)
        empty_layout.addWidget(self.add_button)
        return_button = QtWidgets.QPushButton("Return to Images")
        return_button.clicked.connect(self.images_requested.emit)
        empty_layout.addWidget(return_button)
        empty_layout.addStretch(1)
        layout.addWidget(self.empty)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.editor_host = QtWidgets.QWidget()
        self.editor_layout = QtWidgets.QVBoxLayout(self.editor_host)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.splitter.addWidget(self.editor_host)
        self.preview_panel = QtWidgets.QWidget()
        preview_layout = QtWidgets.QVBoxLayout(self.preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_note = QtWidgets.QLabel()
        self.preview_note.setWordWrap(True)
        preview_layout.addWidget(self.preview_note)
        self.preview_scroll = QtWidgets.QScrollArea()
        self.preview_scroll.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label = QtWidgets.QLabel("No background artwork")
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_scroll.setWidget(self.preview_label)
        preview_layout.addWidget(self.preview_scroll, 1)
        from message_shared_preview import create_shared_preview
        self.shared_preview = create_shared_preview(self.preview_panel, session, cache / f"shared-{uuid4().hex}")
        if self.shared_preview is not None:
            self.shared_preview.status_changed.connect(self.preview_note.setText)
            self.shared_preview.contrast_changed.connect(self._update_preview_contrast)
            preview_layout.addWidget(self.shared_preview, 1)
            self.shared_preview.hide()
            self.preview_device.currentIndexChanged.connect(
                lambda _: self.shared_preview.set_device(self.preview_device.currentData())
            )
        else:
            self.preview_device.hide()
        self.zoom = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.zoom.setRange(25, 200)
        self.zoom.setValue(100)
        self.zoom.setAccessibleName("Preview zoom, view only")
        self.zoom.valueChanged.connect(self._fit_preview)
        self.zoom.hide()
        self.zoom.valueChanged.connect(self.readability_changed)
        self.splitter.addWidget(self.preview_panel)
        self.splitter.setStretchFactor(0, 4)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([800, 260])
        layout.addWidget(self.splitter, 1)
        self.style_panel = ReadabilityControls(self, self, editor=True)
        self.preset = self.style_panel.preset
        self.opacity = self.style_panel.opacity
        layout.addWidget(self.style_panel)
        self.preview_toggle.toggled.connect(self.preview_panel.setVisible)
        self.preview_toggle.toggled.connect(self._preview_visibility_changed)
        self.selector.currentIndexChanged.connect(self._select)
        self.session_changed.connect(self._changed)
        session.add_listener(self.session_changed.emit)
        self.refresh()

    @property
    def page_id(self):
        return self.binding.target.page_id if self.binding is not None else None

    def _letters(self):
        return [(index, page) for index, page in enumerate(self.session.project.pages, 1)
                if page["type"] == "letter"]

    def _label(self, index, page):
        return f"Page {index} of {len(self.session.project.pages)} — {page['name'] or 'Letter'} · Letter"

    @staticmethod
    def _picker_label(label, html):
        return label if message_plain_text(html) or message_resources(html) else f"{label} · Empty"

    def _error(self, message):
        self._failed = True
        self.notice.setText(str(message) + " Your draft remains in this editor. Retry Save Draft or copy it before discarding.")
        self.recover_button.show()

    def _changed(self, event):
        if (self._closed or self._committing or self._refreshing
                or event.session_id != self.session.session_id):
            return
        if event.kind == "prompt":
            return
        if (self._dirty and self.binding is not None
                and self.binding.target.session_id == self.session.session_id):
            try:
                current = self.session.project.get_message(self.page_id)
            except KeyError:
                current = None
            if current is not None and current != self.binding.document:
                self._save_timer.stop()
                self._error("The original message changed before this draft reached disk.")
        self.refresh()

    def refresh(self, requested=None):
        if self._closed or self._refreshing:
            return
        self._refreshing = True
        try:
            letters = self._letters()
            if self._seen_session != self.session.session_id:
                if self._dirty and self.editor is not None:
                    self._save_timer.stop()
                    self._error("The project was replaced before this draft reached disk.")
                    self.heading.setText(f"Retained draft from previous project — {self.binding.label}")
                    return
                self._last_letter = None
                self._views.clear()
                self._recipient_change = None
                self.update_name_button.setToolTip("")
                self._pending_drafts.clear()
            ids = [page["id"] for _, page in letters]
            preferred = requested if requested is not None else self.session.selected_page_id
            selected = preferred if preferred in ids else self._last_letter if self._last_letter in ids else next(iter(ids), None)
            same = (self.binding is not None and self.page_id == selected
                    and self.binding.target.session_id == self.session.session_id)
            document = self.session.project.get_message(selected) if selected else None
            reload_editor = not same or (self.binding is not None and document != self.binding.document)
            if reload_editor and self.editor is not None:
                if self._failed or not self.editor.flush_document():
                    self._error(f"Cannot leave {self.binding.label}. Its page changed or saving failed.")
                    self.heading.setText(f"Retained draft — {self.binding.label}")
                    self._restore_selector()
                    return
                self._dispose_editor()
            if reload_editor:
                self._preview_contrast = None
                self._render_gate.invalidate(accepting=True)
                if self.shared_preview is not None and not same:
                    self.shared_preview.invalidate()
                self._pixmap = QtGui.QPixmap()
                self.preview_label.clear()
                self.preview_label.setText("Updating selected page preview…")
                self.preview_label.adjustSize()
                self.preview_note.clear()
            self._seen_session = self.session.session_id
            with QtCore.QSignalBlocker(self.selector):
                self.selector.clear()
                for index, page in letters:
                    label = self._picker_label(self._label(index, page), page["message"]["html"])
                    self.selector.addItem(label, page["id"])
                    self.selector.setItemData(self.selector.count() - 1, label, QtCore.Qt.ToolTipRole)
                self.selector.setCurrentIndex(self.selector.findData(selected))
            has_letter = selected is not None
            self.empty.setVisible(not has_letter)
            self.splitter.setVisible(has_letter)
            self.actions.setVisible(has_letter)
            self.selector.setEnabled(has_letter)
            self.preview_device.setEnabled(has_letter)
            self.copy_style_button.setEnabled(len(letters) > 1)
            self.style_panel.setVisible(has_letter)
            from project_model import MAX_PAGES
            self.add_button.setEnabled(len(self.session.project.pages) < MAX_PAGES)
            if not has_letter:
                self.binding = None
                self.heading.setText("No Letter pages in this project")
                self._render_gate.invalidate(accepting=True)
                self._pixmap = QtGui.QPixmap()
                self.preview_page_id = None
                self.deactivate_preview()
                self.owner.current_html = ""
                self.readability_changed.emit()
                self._show_recovery_notice()
                return
            index, page = next((i, p) for i, p in letters if p["id"] == selected)
            label = self._label(index, page)
            self.heading.setText(label)
            self._last_letter = selected
            if reload_editor:
                self.binding = _Binding(self.session.capture_target(selected, fields=("type", "message"), include_shared=False), document, label)
                binding = self.binding
                self.editor = Editor(document["html"], parent=self, embedded=True,
                                     save_callback=lambda html: self._commit_html(binding, html),
                                     resource_roots=self._resource_roots(), document_label=label,
                                     missing_resources=self._missing_resources(),
                                     display_settings=self._display_settings(selected),
                                     paper_colors=document.get("settings", {}).get("editor_paper_colors"),
                                     find_callback=self.find_in_letter)
                self.editor.bound_error.connect(self._error)
                self.editor.editor.currentCharFormatChanged.connect(self.readability_changed)
                self.editor.editor.textChanged.connect(self.readability_changed)
                self.editor_layout.addWidget(self.editor)
                self.editor.show()
                view = self._views.get((self.session.session_id, selected))
                if view:
                    cursor = self.editor.editor.textCursor()
                    cursor.setPosition(min(view[0], self.editor.editor.document().characterCount() - 1))
                    cursor.setPosition(min(view[1], self.editor.editor.document().characterCount() - 1), QtGui.QTextCursor.KeepAnchor)
                    self.editor.editor.setTextCursor(cursor)
                    self.editor.editor.verticalScrollBar().setValue(view[2])
                self._failed = False
                self.recover_button.hide()
                self.notice.clear()
            else:
                self.binding.label = label
            self.owner.current_html = document["html"]
            if reload_editor and not document["html"].strip():
                settings = self._display_settings()
                remembered = self.editor.settings.value("paper/lastPreset")
                if ("message_overlay_preset" not in settings and isinstance(remembered, str)
                        and remembered in LETTER_PAGE_PRESET_LABELS):
                    preset, opacity, _, _ = normalized_letter_page_settings({
                        "message_overlay_preset": remembered,
                        "message_overlay_opacity": self.editor.settings.value("paper/lastOpacity", 68),
                    })
                    self.session.project.update_page_settings(selected, {
                        "message_overlay_preset": preset, "message_overlay_opacity": opacity,
                    })
                    self._dirty = True
                    self._save_timer.start()
            self._sync_style()
            self._show_recovery_notice()
            self._preview_timer.start()
        except (ValueError, RuntimeError, OSError, KeyError) as error:
            self._error(str(error))
        finally:
            self._refreshing = False

    def _show_recovery_notice(self):
        recovery = self.session.project.to_dict()["recovery"]
        if recovery.get("unassigned_message") or recovery.get("unresolved_associations"):
            self.notice.setText("Preserved legacy text has no assigned page. Resolve it through project recovery; it has not been attached to a Letter.")

    def _resource_roots(self):
        return tuple(dict.fromkeys(Path(p).resolve() for p in
                                  (self.session.project_root, self.session.asset_root) if p))

    def _missing_resources(self):
        return self.session.project.to_dict()["recovery"].get("missing_resources", [])

    def _asset(self, reference):
        if not isinstance(reference, str) or not reference:
            return None
        if reference in self._missing_resources():
            return None
        path = Path(reference)
        if path.is_absolute() or ".." in path.parts or ":" in reference or "\\" in reference:
            return None
        for root in self._resource_roots():
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file() and not (root / path).is_symlink():
                return candidate
        return None

    @staticmethod
    def _with_html(document, html):
        resources = list(document.get("resources", []))
        for reference in message_resources(html):
            if reference not in resources:
                resources.append(reference)
        return {**document, "html": html, "resources": resources}

    def tool_parent(self):
        return self.editor.window() if self.editor is not None and self.editor.isVisible() else self

    def _review_page(self, index, page):
        page_id = page["id"]
        target = self.session.capture_target(page_id, fields=("type", "message", "$order"), include_shared=False)
        document = QtGui.QTextDocument()
        message = self.session.project.get_message(page_id)
        document.setHtml(sanitize_message_html(message["html"]))
        return _ReviewPage(target, message, f"Page {index} · {page['name'] or 'Letter'}", document.toPlainText())

    def review_pages(self):
        if self.page_id is None or not self.save():
            return ()
        try:
            return tuple(self._review_page(index, page) for index, page in self._letters())
        except (KeyError, ValueError, RuntimeError) as error:
            self.notice.setText(str(error))
            return ()

    def _detached_editor(self, page):
        return Editor(sanitize_message_html(page.document["html"]), parent=self, embedded=True,
                      save_callback=lambda _: True, resource_roots=self._resource_roots(),
                      missing_resources=self._missing_resources(),
                      display_settings=self._display_settings(page.target.page_id),
                      paper_colors=page.document.get("settings", {}).get("editor_paper_colors"))

    def _apply_reviewed_documents(self, pages, documents, notice):
        if not self.save():
            return False

        def apply(model, _page_id):
            if any(not self.session.target_is_current(page.target) for page in pages):
                raise ValueError("Letter pages changed. Open the review again before applying changes.")
            for page_id, document in documents.items():
                model.update_message(page_id, document)

        self._committing = True
        try:
            if not self.session.apply_result(pages[0].target, apply):
                self.notice.setText("Letter pages changed. Open the review again before applying changes.")
                return False
            for page in pages:
                page_id = page.target.page_id
                if page_id in documents:
                    self._queue_draft(page_id, page.document["html"], documents[page_id]["html"])
        except (KeyError, ValueError, RuntimeError) as error:
            self.notice.setText(str(error))
            return False
        finally:
            self._committing = False
        self.refresh(self.page_id)
        self._dirty = True
        if self.save():
            if not self._pending_drafts:
                self.notice.setText(notice)
            self.owner.project_changed.emit()
            return True
        return False

    def apply_text_edits(self, pages, edits, notice):
        documents = {}
        try:
            for page in pages:
                changes = tuple(edit for edit in edits.get(page.target.page_id, ()) if edit.text != edit.replacement)
                if not changes:
                    continue
                detached = self._detached_editor(page)
                try:
                    replace_text_ranges(detached.editor, changes)
                    documents[page.target.page_id] = self._with_html(page.document, detached._prepared_html())
                finally:
                    detached.dispose_document()
                    detached.deleteLater()
        except (KeyError, ValueError, RuntimeError) as error:
            self.notice.setText(str(error))
            return False
        if not documents:
            self.notice.setText("No changes to apply.")
            return True
        return self._apply_reviewed_documents(pages, documents, notice)

    def apply_selection_edits(self, page, edits):
        if (self.editor is None or self.page_id != page.target.page_id or not self.save()
                or not self.session.target_is_current(page.target)
                or self.editor.editor.toPlainText() != page.text):
            self.notice.setText("The selection changed. Reopen Find and Replace to review it.")
            return False
        editor = self.editor.editor
        try:
            changes = tuple(edit for edit in edits if edit.text != edit.replacement)
            with editor.describe_edit("Replace text"):
                replace_text_ranges(editor, changes)
        except ValueError as error:
            self.notice.setText(str(error))
            return False
        return self.save()

    def open_match(self, page, match):
        if not self.session.target_is_current(page.target) or not self.open_page(page.target.page_id):
            return False
        editor = self.editor.editor
        text = editor.toPlainText()
        if text[match.start:match.end] != match.text:
            return False
        offsets = editor._python_to_qt_offsets(text)
        cursor = editor.textCursor()
        cursor.setPosition(offsets[match.start])
        cursor.setPosition(offsets[match.end], QtGui.QTextCursor.KeepAnchor)
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()
        return True

    def find_in_letter(self):
        from message_review_dialog import LetterSearchDialog

        if self.editor is None or not self.save():
            return
        selected = self.editor.editor.textCursor().selectedText()
        dialog = LetterSearchDialog(self, selected if len(selected.splitlines()) == 1 else "")
        dialog.exec()
        dialog.deleteLater()

    def remember_recipient_change(self, previous, new):
        if self._recipient_change and self._recipient_change[1] == previous:
            previous = self._recipient_change[0]
        self._recipient_change = (previous, new) if previous and previous != new else None
        tooltip = f"Change {previous} to {new} on every Letter page." if self._recipient_change else ""
        self.update_name_button.setToolTip(tooltip)
        if self.editor is not None:
            self.editor.recipient_name = new

    def update_name(self):
        pages = self.review_pages()
        if not pages:
            return
        if self._recipient_change:
            self._rewrite_recipient(pages, *self._recipient_change)
            return
        dialog = LetterSmithDialog(self.tool_parent(), title="Update Name in Messages")
        form = QtWidgets.QFormLayout()
        old_name = QtWidgets.QLineEdit()
        new_name = QtWidgets.QLineEdit(self.owner.name_input.text().strip())
        form.addRow("Previous recipient name", old_name)
        form.addRow("New recipient name", new_name)
        dialog.content_layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        update = buttons.addButton("Update Name", QtWidgets.QDialogButtonBox.AcceptRole)

        def check_names():
            before, after = old_name.text().split(), new_name.text().split()
            update.setEnabled(bool(before and after and before != after))

        old_name.textChanged.connect(check_names)
        new_name.textChanged.connect(check_names)
        check_names()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog.content_layout.addWidget(buttons)
        old_name.setFocus()
        old_name.selectAll()
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self._rewrite_recipient(pages, old_name.text(), new_name.text())
        dialog.deleteLater()

    def _rewrite_recipient(self, pages, previous, new):
        edits = {page.target.page_id: recipient_name_edits(page.text, previous, new) for page in pages}
        changed = sum(bool(changes) for changes in edits.values())
        if not changed:
            self.notice.setText("The previous name was not found in the Letter pages.")
            return
        scope = "1 Letter page" if changed == 1 else f"{changed} Letter pages"
        if self.apply_text_edits(pages, edits, f"Recipient name updated on {scope}."):
            self._recipient_change = None
            self.update_name_button.setToolTip("")

    def populate_style_sources(self):
        self.copy_style_menu.clear()
        for index, page in self._letters():
            page_id = page["id"]
            if page_id == self.page_id:
                continue
            action = self.copy_style_menu.addAction(f"Page {index} · {page['name'] or 'Letter'}")
            action.triggered.connect(lambda _checked=False, source=page_id: self.copy_style_from(source))

    def copy_style_from(self, source_id):
        pages = self.review_pages()
        source = next((page for page in pages if page.target.page_id == source_id), None)
        if source is None or self.editor is None or self.page_id == source_id:
            return False
        detached = None
        try:
            detached = self._detached_editor(source)
            if self.editor._unsupported_style_state or detached._unsupported_style_state:
                raise ValueError("Update LetterSmith before copying styles from or to this page.")
            styles = detached.named_styles.page_style_set()
            if not self.session.target_is_current(source.target) or not self.session.target_is_current(self.binding.target):
                raise ValueError("A Letter page changed. Choose the style source again.")
            self.editor.named_styles.apply_page_style_set(styles)
            if self.save():
                self.notice.setText(f"Style copied from {source.label}.")
                return True
        except (KeyError, ValueError, RuntimeError) as error:
            self.notice.setText(str(error))
        finally:
            if detached is not None:
                detached.dispose_document()
                detached.deleteLater()
        return False

    def proofread_letter(self):
        from message_review_dialog import LetterProofreadingDialog

        pages = self.review_pages()
        if not pages or self.editor is None:
            return
        binding = self.editor._proofreading
        try:
            dialog = LetterProofreadingDialog(self, pages, binding.service, binding._terms(), binding._ignored)
        except (ValueError, RuntimeError, OSError) as error:
            self.notice.setText(f"Could not proofread the letter. {error}")
            return
        dialog.exec()
        dialog.deleteLater()

    def _draft_path(self, page_id):
        key = json.dumps([self.session.project.project_id, page_id]).encode()
        return self._history_root / sha256(key).hexdigest()[:32] / "message.html"

    def _queue_draft(self, page_id, before, after):
        first = self._pending_drafts.get(page_id, (before, after))[0]
        if first != after:
            self._pending_drafts[page_id] = (first, after)
        else:
            self._pending_drafts.pop(page_id, None)

    def _write_drafts(self):
        for page_id, (before, after) in tuple(self._pending_drafts.items()):
            path = self._draft_path(page_id)
            try:
                if before.strip():
                    record_draft(path, before, reason="before-edit")
                record_draft(path, after)
            except (OSError, ValueError) as error:
                self.notice.setText(f"Draft saved. Could not write draft history. {error}")
            else:
                del self._pending_drafts[page_id]

    def open_draft_history(self):
        page_id = self.page_id
        if page_id is None or not self.save():
            return
        page = next((self._review_page(index, page) for index, page in self._letters() if page["id"] == page_id), None)
        if page is None:
            return
        path = self._draft_path(page_id)
        dialog = LetterSmithDialog(self.tool_parent(), title=f"Draft History · {page.label}", width=560)
        drafts = QtWidgets.QListWidget()
        drafts.setAccessibleName("Autosaved drafts")
        preview = QtWidgets.QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setAccessibleName("Draft preview")
        dialog.content_layout.addWidget(drafts, 1)
        dialog.content_layout.addWidget(preview, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        restore = buttons.addButton("Restore Draft", QtWidgets.QDialogButtonBox.AcceptRole)
        restore.setEnabled(False)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog.content_layout.addWidget(buttons)
        content = None

        def preview_draft(item, _previous):
            nonlocal content
            try:
                content = read_revision(path, item.data(QtCore.Qt.UserRole)) if item is not None else None
                preview.setPlainText(message_plain_text(content) if content is not None else "")
                restore.setEnabled(content is not None and content != page.document["html"])
            except (OSError, ValueError) as error:
                content = None
                preview.setPlainText(str(error))
                restore.setEnabled(False)

        drafts.currentItemChanged.connect(preview_draft)
        for revision in list_revisions(path):
            item = QtWidgets.QListWidgetItem(revision.display_name, drafts)
            item.setData(QtCore.Qt.UserRole, str(revision.path))
        if drafts.count():
            drafts.setCurrentRow(0)
        else:
            preview.setPlainText("No drafts saved for this page yet.")
        dialog.resize(620, 460)
        if dialog.exec() == QtWidgets.QDialog.Accepted and content is not None:
            self._apply_reviewed_documents((page,), {page_id: self._with_html(page.document, content)}, "Draft restored.")
        dialog.deleteLater()

    def _commit_html(self, binding, html):
        html = sanitize_message_html(html)
        document = dict(binding.document)
        document["html"] = html
        self._preview_contrast = None
        if self.editor is not None:
            document["settings"] = {
                **document.get("settings", {}),
                "editor_paper_colors": self.editor.paper_colors(),
            }
        document = self._with_html(document, html)
        self._committing = True
        try:
            if not self.session.apply_result(binding.target, lambda model, page_id: model.update_message(page_id, document)):
                self._error("The original message changed or its project was replaced. Newer content was not overwritten.")
                return False
            self._queue_draft(binding.target.page_id, binding.document["html"], html)
            binding.document = self.session.project.get_message(binding.target.page_id)
            binding.target = self.session.capture_target(binding.target.page_id, fields=("type", "message"), include_shared=False)
            self.owner.current_html = html
            self._dirty = True
            self._failed = False
            self.recover_button.hide()
            self.notice.setText("Saving draft…")
            index = self.selector.findData(binding.target.page_id)
            if index >= 0:
                label = self._picker_label(binding.label, html)
                self.selector.setItemText(index, label)
                self.selector.setItemData(index, label, QtCore.Qt.ToolTipRole)
            self._save_timer.start()
            self._render_gate.invalidate(accepting=True)
            self._preview_timer.start()
            self.owner.project_changed.emit()
            return True
        except (ValueError, RuntimeError, OSError, KeyError) as error:
            self._error(str(error))
            return False
        finally:
            self._committing = False

    def save(self):
        self._save_timer.stop()
        if self.binding is not None and self.binding.target.session_id != self.session.session_id and (self._dirty or self._failed):
            self._error("This retained draft belongs to the previous project and cannot be written into the new one.")
            return False
        if (self.binding is not None and (self._dirty or self._failed)
                and not self.session.target_is_current(self.binding.target)):
            self._error("The original message changed. This retained draft cannot overwrite newer content.")
            return False
        if self.editor is not None and not self.editor.flush_document():
            return False
        if not self._dirty:
            if not self._failed and self._pending_drafts:
                self._write_drafts()
            return not self._failed
        self._committing = True
        try:
            self.session.save(sync_workspace=False)
            self._dirty = False
            self._failed = False
            self.recover_button.hide()
            self.notice.setText("Draft saved.")
            self._write_drafts()
            return True
        except (ValueError, RuntimeError, OSError) as error:
            self._error(f"Could not save: {error}")
            return False
        finally:
            self._committing = False

    def _restore_selector(self):
        with QtCore.QSignalBlocker(self.selector):
            self.selector.setCurrentIndex(self.selector.findData(self.page_id))

    def _select(self, index):
        page_id = self.selector.itemData(index)
        if page_id is None or page_id == self.page_id:
            return
        if not self.save():
            self._restore_selector()
            return
        self.session.select_page(page_id)
        self.refresh(page_id)

    def open_page(self, page_id):
        if not self.save():
            return False
        ids = [page["id"] for _, page in self._letters()]
        preferred = page_id if page_id is not None else self.session.selected_page_id
        page_id = preferred if preferred in ids else self._last_letter if self._last_letter in ids else next(iter(ids), None)
        # Only explicit entry reconciles a filtered fallback. Hidden Messages
        # refreshes must not hijack the artwork/Prompt Writer editing target.
        if page_id is not None:
            self.session.select_page(page_id)
        self.refresh(page_id)
        if self.editor:
            self.editor.editor.setFocus()
        return not self._failed

    def _dispose_editor(self):
        if self.editor is None:
            return
        if self.binding:
            cursor = self.editor.editor.textCursor()
            self._views[(self.binding.target.session_id, self.page_id)] = (
                cursor.anchor(), cursor.position(), self.editor.editor.verticalScrollBar().value())
        self.editor.dispose_document()
        self.editor_layout.removeWidget(self.editor)
        self.editor.hide()
        self.editor.deleteLater()
        self.editor = None

    def discard_draft(self):
        dialog = LetterSmithConfirmationDialog(self, title="Discard retained draft?",
            question="Discard the text retained in this editor and load the current project's saved document?",
            primary_text="Discard draft", secondary_text="Keep editing", secondary_accepts=True,
            cancel_text="Cancel", click_outside_dismiss=False)
        dialog.exec()
        if dialog.choice == "primary":
            self._dispose_editor()
            self.binding = None
            self._failed = False
            self._dirty = False
            self._pending_drafts.clear()
            self.refresh()

    def clear_text(self):
        if self.editor is None:
            return
        dialog = LetterSmithConfirmationDialog(self, title="Clear Text",
            question=f"Clear only the message in {self.binding.label}? Its artwork and prompt will remain.",
            primary_text="Clear Text", secondary_text="Keep Text", secondary_accepts=True,
            cancel_text="Cancel", click_outside_dismiss=False)
        dialog.exec()
        if dialog.choice == "primary":
            self.editor.editor.selectAll()
            self.editor.editor.textCursor().removeSelectedText()
            self.save()

    def add_letter(self):
        if not self.save():
            return
        try:
            identity = self.session.project.add_page("letter")
            self._dirty = True
            self.open_page(identity)
        except (ValueError, RuntimeError) as error:
            self.notice.setText(str(error))

    def _edit_background(self):
        if self.page_id and self.save():
            self.background_requested.emit(self.page_id)

    def _sync_style(self):
        if not self.page_id:
            return
        if self.editor is not None:
            self.editor.set_display_settings(self._display_settings())
        self.readability_changed.emit()

    def _update_preview_contrast(self, ratio):
        self._preview_contrast = ratio
        self.readability_changed.emit()

    def _display_settings(self, page_id=None):
        page_id = page_id or self.page_id
        if page_id is None:
            return {}
        return {**self.session.project.resolve_settings(page_id),
                **self.session.project.get_message(page_id).get("settings", {})}

    def edit_layout(self):
        if not self.page_id or not self.save():
            return
        from message_layout_dialog import MessageLayoutDialog
        page_id = self.page_id
        target = self.session.capture_target(page_id, fields=("type", "message", "artwork", "design_overrides"))
        dialog = MessageLayoutDialog(
            self, self.session.project.get_page(page_id), self.session.project.resolve_settings(page_id),
            self.session.project.to_dict()["shared_settings"], self.binding.label,
            lambda geometry, **options: self.apply_layout(target, geometry, **options),
        )
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def apply_layout(self, target, geometry, *, use_default=False):
        from copy import deepcopy
        from project_model import resolve_page_layout

        if self._failed or self.page_id != target.page_id:
            raise RuntimeError("The edited page changed. Reopen Text Layout for the intended Letter.")

        def apply(model, page_id):
            page = model.get_page(page_id)
            document = page["message"]
            document["layout"] = {} if use_default else deepcopy(geometry)
            settings = model.resolve_settings(page_id)
            if use_default:
                settings.pop("message_layout", None)
                defaults = model.to_dict()["shared_settings"]
                if "message_layout" in defaults:
                    settings["message_layout"] = defaults["message_layout"]
            resolve_page_layout(page, settings)
            model.update_message(page_id, document)
            if use_default:
                model.clear_page_override(page_id, "message_layout")

        self._committing = True
        try:
            if not self.session.apply_result(target, apply):
                raise RuntimeError("The page or its defaults changed while Text Layout was open. Reopen it to review the current values.")
            self.binding.document = self.session.project.get_message(self.page_id)
            self.binding.target = self.session.capture_target(self.page_id, fields=("type", "message"), include_shared=False)
            self._dirty = True
            self.owner.project_changed.emit()
        finally:
            self._committing = False
        self.refresh()
        self.save()

    def set_readability(self, patch):
        if self.binding is None or self._failed:
            return
        def apply(model, page_id):
            model.update_page_settings(page_id, patch)
            document = model.get_message(page_id)
            # Older documents may carry a more specific presentation override.
            if any(key in document.get("settings", {}) for key in patch):
                document["settings"] = {**document["settings"], **patch}
                model.update_message(page_id, document)
        self._apply_style(apply)
        if not self._failed and self.editor is not None:
            preset, opacity, _, _ = normalized_letter_page_settings(self._display_settings())
            self.editor.settings.setValue("paper/lastPreset", preset)
            self.editor.settings.setValue("paper/lastOpacity", opacity)

    def customize_style(self, *_):
        if self.binding is None or self._refreshing or self._failed:
            return
        patch = {"message_overlay_preset": self.preset.currentData(), "message_overlay_opacity": self.opacity.value()}
        self.set_readability(patch)

    def use_project_style(self):
        if self.binding is None or self._failed:
            return
        def reset(model, page_id):
            for key in ("message_overlay_preset", "message_overlay_opacity"):
                model.clear_page_override(page_id, key)
        self._apply_style(reset)

    def _apply_style(self, callback):
        self._preview_contrast = None
        self._committing = True
        try:
            if not self.session.apply_result(self.binding.target, callback):
                self._error("The original page changed. Its readability settings were not overwritten.")
                return
            self.binding.document = self.session.project.get_message(self.page_id)
            self.binding.target = self.session.capture_target(self.page_id, fields=("type", "message"), include_shared=False)
            self._dirty = True
            self._save_timer.start()
        except (ValueError, RuntimeError, OSError, KeyError) as error:
            self._error(str(error))
        finally:
            self._committing = False
        self.refresh()
        self.owner.project_changed.emit()

    def capture_import(self, path, *, mode=None):
        if not self.page_id or not self.save():
            return None
        if mode is None:
            dialog = LetterSmithConfirmationDialog(self,
                title=f"Import Text into {self.binding.label}",
                question="Replace the existing text or append to it? Supported formatting is preserved; PDF/ODT and DOCX fallback conversions may lose layout. Unsafe or missing images will be reported before applying.",
                primary_text="Replace", secondary_text="Append", secondary_accepts=True,
                cancel_text="Cancel", click_outside_dismiss=False, width=540)
            dialog.exec()
            mode = {"primary": "replace", "secondary": "append"}.get(dialog.choice)
        if mode not in {"replace", "append"}:
            return None
        target = self.session.capture_target(self.page_id, fields=("type", "message"), include_shared=False)
        self._pending_import = (target, self.binding.label, mode)
        self.notice.setText(f"Import Text into {self.binding.label}")
        return target

    def finish_import(self, path, html, target):
        request = self._pending_import
        self._pending_import = None
        if request is None or request[0] != target:
            return False
        try:
            staged = stage_import_html(html, source_path=path, project_root=self.project_root)
        except (ValueError, RuntimeError, OSError) as error:
            self.notice.setText(f"Import could not be staged: {error}")
            return False
        if self._review_import:
            self._review_import[1].cleanup()
        self._review_import = (request, staged)
        self.review_button.show()
        if not self.session.target_is_current(target):
            self.notice.setText(f"Import for {request[1]} is retained for review. Its message changed or project was replaced.")
            return False
        if staged.notices:
            self.notice.setText("Import needs review: " + " ".join(staged.notices))
            return False
        return self.apply_reviewed_import()

    def apply_reviewed_import(self, *, resolve_conflict=False):
        if not self._review_import:
            return False
        (target, label, mode), staged = self._review_import
        if resolve_conflict:
            if target.session_id != self.session.session_id:
                self.notice.setText("The original project is no longer active. The imported text remains available to copy from Review Import.")
                return False
            try:
                target = self.session.capture_target(target.page_id, fields=("type", "message"), include_shared=False)
            except (ValueError, KeyError):
                return False
        if not self.session.target_is_current(target):
            self.notice.setText("The original message changed. Review the import before applying it.")
            return False
        original = self.session.project.get_message(target.page_id)
        if original is None:
            return False
        # Use the capable editor's own document/style serialization for append.
        editor = None
        try:
            merged = dict(original)
            merged["html"] = staged.html
            if mode == "append":
                editor = Editor(original["html"], parent=self, embedded=True,
                                save_callback=lambda _: True, resource_roots=self._resource_roots(),
                                missing_resources=self._missing_resources())
                cursor = editor.editor.textCursor()
                cursor.movePosition(QtGui.QTextCursor.End)
                cursor.insertBlock()
                cursor.insertHtml(staged.html)
                merged["html"] = editor._prepared_html()
            resources = list(original.get("resources", []))
            for resource in staged.resources:
                if resource not in resources:
                    resources.append(resource)
            merged["resources"] = resources
            self._committing = True
            def apply(model, identity):
                staged.commit_resources()
                model.update_message(identity, merged)
            if not self.session.apply_result(target, apply):
                return False
            self._queue_draft(target.page_id, original["html"], merged["html"])
            self._dirty = True
            self._review_import = None
            self.review_button.hide()
            staged.cleanup()
        except (ValueError, RuntimeError, OSError) as error:
            self.notice.setText(f"Import retained for review: {error}")
            return False
        finally:
            self._committing = False
            if editor:
                editor.dispose_document()
                editor.deleteLater()
        self.refresh()
        saved = self.save()
        if saved:
            self.notice.setText(f"Text imported into {label}.")
        return saved

    def review_import(self):
        if not self._review_import:
            return
        request, staged = self._review_import
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"Review import — {request[1]}")
        layout = QtWidgets.QVBoxLayout(dialog)
        label = QtWidgets.QLabel(" ".join(staged.notices) or "Review the imported text before applying it to the original page.")
        label.setWordWrap(True)
        layout.addWidget(label)
        view = QtWidgets.QTextEdit()
        view.setReadOnly(True)
        # Review plain text avoids resolving staged resources before commit.
        doc = QtGui.QTextDocument()
        doc.setHtml(staged.html)
        view.setPlainText(doc.toPlainText())
        layout.addWidget(view)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        apply_button = buttons.addButton(f"{request[2].title()} original message", QtWidgets.QDialogButtonBox.AcceptRole)
        apply_button.setEnabled(request[0].session_id == self.session.session_id)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(600, 450)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.apply_reviewed_import(resolve_conflict=True)

    def render_preview(self):
        self._preview_timer.stop()
        self._render_gate.invalidate(accepting=True)
        self._pixmap = QtGui.QPixmap()
        if not self.page_id or self._failed:
            return
        if self.shared_preview is not None and not self.preview_panel.isVisible():
            return
        snapshot = self.session.project.render_snapshot()
        page = next((p for p in snapshot["pages"] if p["id"] == self.page_id), None)
        if page is None or page["message"] is None:
            return
        self.preview_page_id = page["id"]
        self.preview_html = page["message"]["html"]
        self.preview_background = page["artwork"].get("asset")
        background = self._asset(self.preview_background)
        if self.editor is not None:
            self.editor.set_background_artwork(QtGui.QPixmap(str(background)) if background else None)
            self.readability_changed.emit()
        self.preview_label.clear()
        if background is None:
            if self.shared_preview is not None:
                self.shared_preview.invalidate()
                self.shared_preview.hide()
                self.preview_scroll.show()
            self.preview_label.setText("No background artwork — draft remains editable")
            self.preview_label.adjustSize()
            self.preview_note.setText("Choose background artwork in Images.")
            return
        if self.shared_preview is not None:
            geometry = page.get("resolved_layout") or {}
            canvas = (geometry.get("artwork") or geometry.get("message") or {}).get("canvas")
            if canvas:
                self.shared_preview.set_page_aspect(canvas["width"], canvas["height"])
            else:
                size = QtGui.QImageReader(str(background)).size()
                self.shared_preview.set_page_aspect(size.width(), size.height())
            self.preview_scroll.hide()
            self.shared_preview.show()
            self.shared_preview.request(page["id"])
            return
        if page["message"].get("layout") or page["message"].get("settings") or page["artwork"].get("placement"):
            self.preview_label.setText("Saved layout requires the page-aware Forge renderer")
            self.preview_label.adjustSize()
            self.preview_note.setText("Text and layout are preserved. Coordinate controls await the shared layout contract.")
            return
        note = "Static preview using the existing Messages renderer."
        if background.suffix.lower() == ".gif":
            note += " GIF shown as a still; animation verification requires Forge."
        missing = [r for r in message_resources(page["message"]["html"]) if self._asset(r) is None]
        if missing:
            note += " Missing embedded resource: " + ", ".join(missing)
        self.preview_note.setText(note)
        from Message_tab import _MessageRenderTask
        revision = self._render_gate.next_revision()
        target = self.session.capture_target(page["id"], fields=("type", "message", "artwork", "design_overrides"))
        preset, opacity, _, _ = normalized_letter_page_settings(page["resolved_settings"])
        task = _MessageRenderTask(revision=revision, gate=self._render_gate,
            html=page["message"]["html"], wall_path=background, output_path=self._preview_path,
            staged_path=self._preview_path.with_name(f"{self._preview_path.stem}-{revision}.png"),
            preset=preset, overlay_opacity=opacity, resource_roots=self._resource_roots(),
            missing_resources=self._missing_resources())
        self._render_tasks[revision] = (task, target, note)
        task.signals.finished.connect(self._preview_finished)
        self._render_pool.start(task)

    def _preview_finished(self, revision, committed, error):
        item = self._render_tasks.pop(revision, None)
        if item is None or self._closed:
            return
        task, target, note = item
        if self.page_id != target.page_id or not self.session.target_is_current(target) or not self._render_gate.is_current(revision):
            return
        if error:
            self.preview_note.setText("Preview unavailable: " + error)
        elif committed:
            self._pixmap = QtGui.QPixmap(str(self._preview_path))
            if task.diagnostics.get("overflow"):
                note += " Text exceeds this fixed preview. All text is saved; adjust formatting. Interactive scrolling requires Forge verification."
            self.preview_note.setText(note)
            self._fit_preview()

    def _fit_preview(self, *_):
        if self.shared_preview is not None:
            self.shared_preview.set_zoom(self.zoom.value())
        if self._pixmap.isNull():
            return
        viewport = self.preview_scroll.viewport().size()
        fit = min(max(1, viewport.width() - 8) / self._pixmap.width(),
                  max(1, viewport.height() - 8) / self._pixmap.height())
        size = self._pixmap.size() * (fit * self.zoom.value() / 100)
        self.preview_label.setPixmap(self._pixmap.scaled(size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        self.preview_label.resize(size)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_preview()

    def _preview_visibility_changed(self, visible):
        if visible:
            self._preview_timer.start()
        else:
            self.deactivate_preview()

    def deactivate_preview(self):
        self._preview_timer.stop()
        if self.shared_preview is not None:
            self.shared_preview.invalidate()

    def prepare_for_restore(self):
        if not self.save():
            raise RuntimeError("Save or recover the retained message draft before changing projects.")
        self._preview_timer.stop()
        self._render_gate.invalidate(accepting=True)
        self._render_pool.clear()
        if not self._render_pool.waitForDone(5000):
            raise RuntimeError("Message preview is still running.")
        if self.shared_preview is not None and not self.shared_preview.release():
            raise RuntimeError("Shared Messages preview is still releasing its files.")

    def shutdown(self):
        if self._closed:
            return True
        if not self.save():
            return False
        if self.shared_preview is not None and not self.shared_preview.shutdown():
            return False
        self._save_timer.stop()
        self._preview_timer.stop()
        self._render_gate.invalidate(accepting=False)
        self._render_pool.clear()
        stopped = self._render_pool.waitForDone(5000)
        if not stopped:
            return False
        self._closed = True
        self.session.remove_listener(self.session_changed.emit)
        self._dispose_editor()
        if self._review_import:
            self._review_import[1].cleanup()
        return stopped
