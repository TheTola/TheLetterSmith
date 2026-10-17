"""Page-bound Messages UI; authored documents remain in ProjectSession."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PySide6 import QtCore, QtGui, QtWidgets

from Editor import Editor
from letter_page import LETTER_PAGE_PRESET_LABELS, normalized_letter_page_settings
from message_document_import import message_resources, stage_import_html
from message_html import sanitize_message_html
from project_paths import application_paths
from ui_dialogs import LetterSmithConfirmationDialog


@dataclass
class _Binding:
    target: object
    document: dict
    label: str


class MessagesWorkspace(QtWidgets.QWidget):
    """One capable editor, selected by permanent page ID, with transient view state."""

    session_changed = QtCore.Signal(object)
    background_requested = QtCore.Signal(str)
    images_requested = QtCore.Signal()

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
        cache = application_paths(self.project_root).cache_root / "messages"
        cache.mkdir(parents=True, exist_ok=True)
        self._preview_path = cache / f"{uuid4().hex}.png"

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QtWidgets.QHBoxLayout()
        self.selector = QtWidgets.QComboBox()
        self.selector.setAccessibleName("Letter page to edit")
        self.selector.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.selector.setMinimumContentsLength(18)
        header.addWidget(self.selector, 1)
        self.background_button = QtWidgets.QPushButton("Edit Background Artwork")
        self.background_button.clicked.connect(self._edit_background)
        header.addWidget(self.background_button)
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
        self.import_button = QtWidgets.QPushButton("Import Text")
        self.import_button.clicked.connect(owner.select_file)
        self.clear_button = QtWidgets.QPushButton("Clear Text")
        self.clear_button.clicked.connect(self.clear_text)
        self.save_button = QtWidgets.QPushButton("Save Draft")
        self.save_button.clicked.connect(self.save)
        self.review_button = QtWidgets.QPushButton("Review Import")
        self.review_button.clicked.connect(self.review_import)
        self.review_button.hide()
        self.recover_button = QtWidgets.QPushButton("Discard draft and reload")
        self.recover_button.clicked.connect(self.discard_draft)
        self.recover_button.hide()
        for widget in (self.import_button, self.clear_button, self.save_button,
                       self.recover_button):
            actions.addWidget(widget)
        actions.addStretch(1)
        self.layout_button = QtWidgets.QPushButton("Text Layout")
        self.layout_button.clicked.connect(self.edit_layout)
        actions.addWidget(self.layout_button)
        self.style_toggle = QtWidgets.QPushButton("Readability")
        self.style_toggle.setCheckable(True)
        actions.addWidget(self.style_toggle)
        layout.addWidget(self.actions)
        # A delayed import can outlive every Letter page in the active project.
        layout.addWidget(self.review_button, 0, QtCore.Qt.AlignLeft)

        self.style_panel = QtWidgets.QWidget()
        style = QtWidgets.QHBoxLayout(self.style_panel)
        style.setContentsMargins(0, 0, 0, 0)
        self.style_scope = QtWidgets.QLabel()
        style.addWidget(self.style_scope)
        self.preset = QtWidgets.QComboBox()
        self.preset.setAccessibleName("This page's readability treatment")
        for key, label in LETTER_PAGE_PRESET_LABELS.items():
            self.preset.addItem(label, key)
        style.addWidget(self.preset)
        self.opacity = QtWidgets.QSpinBox()
        self.opacity.setRange(0, 100)
        self.opacity.setSuffix("%")
        self.opacity.setAccessibleName("This page's readability opacity")
        style.addWidget(self.opacity)
        self.default_button = QtWidgets.QPushButton("Use project default")
        self.default_button.clicked.connect(self.use_project_style)
        style.addWidget(self.default_button)
        self.style_panel.hide()
        layout.addWidget(self.style_panel)
        self.style_toggle.toggled.connect(self.style_panel.setVisible)
        self.preset.currentIndexChanged.connect(self.customize_style)
        self.opacity.valueChanged.connect(self.customize_style)

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
            preview_layout.addWidget(self.shared_preview, 1)
            self.shared_preview.hide()
        self.zoom = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.zoom.setRange(25, 200)
        self.zoom.setValue(100)
        self.zoom.setAccessibleName("Preview zoom, view only")
        self.zoom.valueChanged.connect(self._fit_preview)
        preview_layout.addWidget(self.zoom)
        self.splitter.addWidget(self.preview_panel)
        self.splitter.setStretchFactor(0, 4)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([800, 260])
        layout.addWidget(self.splitter, 1)
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

    @staticmethod
    def _label(index, page):
        return f"Page {index} — {page['name'] or 'Letter'}"

    def _error(self, message):
        self._failed = True
        self.notice.setText(str(message) + " Your draft remains in this editor. Retry Save Draft or copy it before discarding.")
        self.recover_button.show()

    def _changed(self, event):
        if self._closed or self._committing or self._refreshing:
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
                self._render_gate.invalidate(accepting=True)
                if self.shared_preview is not None:
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
                    self.selector.addItem(self._label(index, page), page["id"])
                self.selector.setCurrentIndex(self.selector.findData(selected))
            has_letter = selected is not None
            self.empty.setVisible(not has_letter)
            self.splitter.setVisible(has_letter)
            self.actions.setVisible(has_letter)
            self.background_button.setEnabled(has_letter)
            self.selector.setEnabled(has_letter)
            self.style_panel.setVisible(has_letter and self.style_toggle.isChecked())
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
                self._show_recovery_notice()
                return
            index, page = next((i, p) for i, p in letters if p["id"] == selected)
            label = self._label(index, page)
            self.heading.setText(label)
            self.import_button.setToolTip(f"Import Text into {label}")
            self._last_letter = selected
            if reload_editor:
                self.binding = _Binding(self.session.capture_target(selected, fields=("type", "message"), include_shared=False), document, label)
                binding = self.binding
                self.editor = Editor(document["html"], parent=self, embedded=True,
                                     save_callback=lambda html: self._commit_html(binding, html),
                                     resource_roots=self._resource_roots(), document_label=label,
                                     display_settings=self.session.project.resolve_settings(selected))
                self.editor.bound_error.connect(self._error)
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

    def _asset(self, reference):
        if not isinstance(reference, str) or not reference:
            return None
        path = Path(reference)
        if path.is_absolute() or ".." in path.parts or ":" in reference or "\\" in reference:
            return None
        for root in self._resource_roots():
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file() and not (root / path).is_symlink():
                return candidate
        return None

    def _commit_html(self, binding, html):
        html = sanitize_message_html(html)
        document = dict(binding.document)
        document["html"] = html
        resources = list(document.get("resources", []))
        for reference in message_resources(html):
            if reference not in resources:
                resources.append(reference)
        document["resources"] = resources
        self._committing = True
        try:
            if not self.session.apply_result(binding.target, lambda model, page_id: model.update_message(page_id, document)):
                self._error("The original message changed or its project was replaced. Newer content was not overwritten.")
                return False
            binding.document = self.session.project.get_message(binding.target.page_id)
            binding.target = self.session.capture_target(binding.target.page_id, fields=("type", "message"), include_shared=False)
            self.owner.current_html = html
            self._dirty = True
            self._failed = False
            self.recover_button.hide()
            self.notice.setText("Saving draft…")
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
            return not self._failed
        self._committing = True
        try:
            self.session.save(sync_workspace=False)
            self._dirty = False
            self._failed = False
            self.recover_button.hide()
            self.notice.setText("Draft saved.")
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
        if page_id is not None:
            self.session.select_page(page_id)
        self.refresh(page_id)
        if self.editor:
            self.editor.editor.setFocus()
        return True

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
        page = self.session.project.get_page(self.page_id)
        preset, opacity, _, _ = normalized_letter_page_settings(self.session.project.resolve_settings(self.page_id))
        with QtCore.QSignalBlocker(self.preset), QtCore.QSignalBlocker(self.opacity):
            self.preset.setCurrentIndex(self.preset.findData(preset))
            self.opacity.setValue(opacity)
        customized = any(key in page["design_overrides"] for key in ("message_overlay_preset", "message_overlay_opacity"))
        self.style_scope.setText("Customized for this page" if customized else "Using project text style")
        self.default_button.setEnabled(customized)
        if self.editor is not None:
            self.editor.set_display_settings(self.session.project.resolve_settings(self.page_id))

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

    def customize_style(self, *_):
        if self.binding is None or self._refreshing or self._failed:
            return
        patch = {"message_overlay_preset": self.preset.currentData(), "message_overlay_opacity": self.opacity.value()}
        self._apply_style(lambda model, page_id: model.update_page_settings(page_id, patch))

    def use_project_style(self):
        if self.binding is None or self._failed:
            return
        def reset(model, page_id):
            for key in ("message_overlay_preset", "message_overlay_opacity"):
                model.clear_page_override(page_id, key)
        self._apply_style(reset)

    def _apply_style(self, callback):
        self._committing = True
        try:
            if not self.session.apply_result(self.binding.target, callback):
                self._error("The original page changed. Its readability settings were not overwritten.")
                return
            self._dirty = True
            self._save_timer.start()
        except (ValueError, RuntimeError, OSError, KeyError) as error:
            self._error(str(error))
        finally:
            self._committing = False
        self.refresh()

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
                                save_callback=lambda _: True, resource_roots=self._resource_roots())
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
            preset=preset, overlay_opacity=opacity, resource_roots=self._resource_roots())
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
