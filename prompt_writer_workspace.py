"""Full-page presentation of the existing, canonical Prompt Writer controller."""
import uuid

from PySide6 import QtCore, QtGui, QtWidgets

from PromptWriterPanel import PromptWriterPanel as _PromptWriterPanel
from prompt_writer_project import page_label
from ui_dialogs import LetterSmithConfirmationDialog


class PromptWriterPanel(_PromptWriterPanel):
    def __init__(self, parent=None, **kwargs):
        self._workspace_ready = False
        self._page_fields = {}
        self._fields_session = None
        self._image_destination = None
        self._host = parent
        super().__init__(parent, **kwargs)
        self._visionary_timer.stop()
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        if getattr(parent, 'application_stack', None) is not None:
            self.setWindowFlags(QtCore.Qt.Widget)
            self.title_bar.window_controller.shutdown()
        self.setMinimumSize(480, 350)
        self._workspace_ready = True
        self.reload_project_state()

    def _build_ui(self):
        # Retain the established controls/controller privately during this
        # reversible presentation change. Reuse shared inputs, not their data.
        super()._build_ui()
        root = self.layout()
        while root.count():
            item = root.takeAt(0)
            if item.widget():
                item.widget().hide()
        root.setContentsMargins(22, 14, 22, 14)
        root.setSpacing(12)
        heading = QtWidgets.QHBoxLayout()
        self.back_button = QtWidgets.QPushButton('‹ Images')
        self.back_button.clicked.connect(self._on_close)
        heading.addWidget(self.back_button)
        title = QtWidgets.QLabel('Prompt Writer')
        font = title.font()
        font.setPointSize(19)
        font.setBold(True)
        title.setFont(font)
        heading.addWidget(title)
        heading.addStretch()
        self.prompts_button = QtWidgets.QPushButton('Prompts')
        self.prompts_button.clicked.connect(lambda: self._output_stack.setCurrentWidget(self._preview_scroll))
        self.service_button = QtWidgets.QPushButton('Image Service')
        self.service_button.clicked.connect(self._open_service)
        heading.addWidget(self.prompts_button)
        heading.addWidget(self.service_button)
        root.addLayout(heading)
        self.workspace_status = QtWidgets.QLabel()
        self.workspace_status.setWordWrap(True)
        self.workspace_status.setTextFormat(QtCore.Qt.PlainText)
        root.addWidget(self.workspace_status)

        self._preview_scroll = QtWidgets.QScrollArea()
        self._preview_scroll.setWidgetResizable(True)
        self._preview_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._preview_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        content = QtWidgets.QWidget()
        content.setObjectName('WriterContent')
        form = QtWidgets.QVBoxLayout(content)
        form.setContentsMargins(2, 2, 14, 12)
        form.setSpacing(18)
        shared = QtWidgets.QGroupBox('Project style')
        shared_form = QtWidgets.QGridLayout(shared)
        shared_form.setHorizontalSpacing(16)
        for column, (label, combo) in enumerate((('Subject', self.cmb_subject), ('Illustration style', self.cmb_type), ('Palette', self.cmb_color))):
            combo.setMinimumSize(100, 36)
            combo.setMaximumSize(16777215, 16777215)
            combo.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
            combo.setAccessibleName(label)
            shared_form.addWidget(QtWidgets.QLabel(label), 0, column)
            shared_form.addWidget(combo, 1, column)
            shared_form.setColumnStretch(column, 1)
            combo.show()
        form.addWidget(shared)
        guidance = QtWidgets.QToolButton()
        guidance.setText('Helpful guidance')
        guidance.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        guidance.setArrowType(QtCore.Qt.RightArrow)
        guidance.setCheckable(True)
        guidance.toggled.connect(lambda checked: (self.gb_helpful.setVisible(checked), guidance.setArrowType(QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow)))
        form.addWidget(guidance)
        form.addWidget(self.gb_helpful)
        self.gb_helpful.hide()
        form.addWidget(QtWidgets.QLabel('Apply to all images'))
        self.txt_global.setMinimumHeight(145)
        self.txt_global.setMaximumHeight(260)
        self.txt_global.setAccessibleName('Apply to all images')
        form.addWidget(self.txt_global)
        self.txt_global.show()
        form.addWidget(QtWidgets.QLabel('Directions by page'))
        self.directions_container = QtWidgets.QWidget()
        self.directions_grid = QtWidgets.QGridLayout(self.directions_container)
        self.directions_grid.setContentsMargins(0, 0, 0, 0)
        self.directions_grid.setSpacing(16)
        form.addWidget(self.directions_container)
        build_row = QtWidgets.QHBoxLayout()
        self.build_button = QtWidgets.QPushButton('Build Prompts')
        self.build_button.setObjectName('primaryButton')
        self.build_button.clicked.connect(lambda: self._build_pages([p['id'] for p in self.session.project.pages]))
        build_row.addWidget(self.build_button)
        build_row.addStretch()
        form.addLayout(build_row)
        self.output_heading = QtWidgets.QLabel('Generated prompts')
        form.addWidget(self.output_heading)
        self.outputs_container = QtWidgets.QWidget()
        self.outputs_layout = QtWidgets.QVBoxLayout(self.outputs_container)
        self.outputs_layout.setContentsMargins(0, 0, 0, 0)
        self.outputs_layout.setSpacing(16)
        form.addWidget(self.outputs_container)
        form.addStretch()
        self._preview_scroll.setWidget(content)
        self._output_stack = QtWidgets.QStackedWidget()
        self._output_stack.addWidget(self._preview_scroll)
        self.conversation_bar = QtWidgets.QWidget()
        destination_row = QtWidgets.QHBoxLayout(self.conversation_bar)
        destination_row.setContentsMargins(0, 0, 0, 0)
        destination_row.addWidget(QtWidgets.QLabel('New images for'))
        self.destination_selector = QtWidgets.QComboBox()
        self.destination_selector.setAccessibleName('Destination for new conversation images')
        self.destination_selector.activated.connect(lambda: self._bind_image_target(self.destination_selector.currentData()))
        destination_row.addWidget(self.destination_selector, 1)
        destination_row.addStretch()
        root.addWidget(self.conversation_bar)
        self.conversation_bar.hide()
        self._output_stack.currentChanged.connect(lambda: self.conversation_bar.setVisible(self._output_stack.currentWidget() is not self._preview_scroll))
        root.addWidget(self._output_stack, 1)
        # Existing async import/submission handlers already report here.
        root.addWidget(self._site_status)
        self._site_status.setWordWrap(True)

    def _apply_styles(self):
        tokens = getattr(self, '_theme_tokens', None)
        background = getattr(tokens, 'background', self.palette().window().color().name())
        text = getattr(tokens, 'text', self.palette().windowText().color().name())
        border = getattr(tokens, 'border', self.palette().mid().color().name())
        self.setStyleSheet(f'QWidget#WriterContent {{ background:{background}; color:{text}; }}'
                          f'QGroupBox {{ color:{text}; border-color:{border}; }}' + '''
            QScrollArea { border: none; background: transparent; }
            QGroupBox { border: 1px solid palette(mid); border-radius: 6px;
                        margin-top: 10px; padding: 14px 10px 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPlainTextEdit, QTextEdit { padding: 8px; }
            QPushButton { min-height: 26px; padding: 4px 12px; }
        ''')

    def _persist_state_now(self):
        saved = super()._persist_state_now()
        if not saved and self._workspace_ready:
            self.workspace_status.setText('Save failed. Your prompt edits are still open; retry before leaving.')
        return saved

    def reload_project_state(self):
        result = super().reload_project_state()
        if result and self._workspace_ready:
            self._refresh_pages()
        return result

    def _refresh_pages(self):
        pages = self.session.project.pages
        with QtCore.QSignalBlocker(self.destination_selector):
            self.destination_selector.clear()
            for page in pages:
                self.destination_selector.addItem(page_label(self.session.project, page['id']), page['id'])
            self.destination_selector.setCurrentIndex(self.destination_selector.findData(self._image_destination or self._view_page_id))
        ids = [p['id'] for p in pages]
        new_session = self._fields_session != self.session.session_id
        if new_session or list(self._page_fields) != ids:
            for fields in self._page_fields.values():
                for card in (fields['directions_card'], fields['output_card']):
                    card.hide()
                    card.setParent(None)
                    card.deleteLater()
            self._page_fields = {}
            self._fields_session = self.session.session_id
            for page in pages:
                page_id = page['id']
                directions_card = QtWidgets.QGroupBox()
                direction_layout = QtWidgets.QVBoxLayout(directions_card)
                directions = QtWidgets.QPlainTextEdit()
                directions.setMinimumHeight(145)
                directions.setMaximumHeight(220)
                directions.setPlaceholderText('Describe the scene and composition for this page.')
                direction_layout.addWidget(directions)
                output_card = QtWidgets.QGroupBox()
                output_layout = QtWidgets.QVBoxLayout(output_card)
                output = QtWidgets.QPlainTextEdit()
                output.setMinimumHeight(230)
                output.setMaximumHeight(360)
                output.setPlaceholderText('Build prompts to create the text for this page.')
                output_layout.addWidget(output)
                actions = QtWidgets.QHBoxLayout()
                status = QtWidgets.QLabel()
                status.setWordWrap(True)
                actions.addWidget(status, 1)
                copy = QtWidgets.QPushButton('Copy')
                copy.clicked.connect(lambda checked=False, key=page_id: self._copy_prompt(key))
                send = QtWidgets.QPushButton('Create image')
                send.clicked.connect(lambda checked=False, key=page_id: self._send_page(key))
                actions.addWidget(copy)
                actions.addWidget(send)
                output_layout.addLayout(actions)
                self.outputs_layout.addWidget(output_card)
                fields = dict(directions_card=directions_card, directions=directions,
                              output_card=output_card, output=output, status=status, copy=copy, send=send)
                self._page_fields[page_id] = fields
                for key, editor in (('directions', directions), ('text', output)):
                    editor.textChanged.connect(lambda pid=page_id, field=key, widget=editor, sid=self._fields_session:
                                               self._edit_page(pid, field, widget, sid))
        for page in pages:
            fields = self._page_fields[page['id']]
            label = page_label(self.session.project, page['id'])
            fields['directions_card'].setTitle(label)
            fields['output_card'].setTitle(label)
            for key, editor in (('directions', fields['directions']), ('text', fields['output'])):
                with QtCore.QSignalBlocker(editor):
                    self._restore_text(editor, page['prompt'][key] or '', new_session)
                editor.setAccessibleName(label + (' directions' if key == 'directions' else ' prompt'))
        self._arrange_directions()
        self._refresh_workspace_status()

    def _arrange_directions(self):
        columns = 2 if self.width() >= 850 else 1
        for index, fields in enumerate(self._page_fields.values()):
            self.directions_grid.addWidget(fields['directions_card'], index // columns, index % columns)
        self.directions_grid.setColumnStretch(0, 1)
        self.directions_grid.setColumnStretch(1, 1 if columns == 2 else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._workspace_ready:
            self._arrange_directions()

    def _edit_page(self, page_id, key, editor, session_id):
        if self._state_persistence_suspended or self.session.is_restoring or session_id != self.session.session_id:
            return
        if page_id not in {p['id'] for p in self.session.project.pages}:
            return
        self._writing = True
        try:
            self.session.project.update_prompt(page_id, {key: editor.toPlainText()})
        finally:
            self._writing = False
        self._authored_change()

    def _refresh_status(self):
        super()._refresh_status()
        if self._workspace_ready:
            self._refresh_workspace_status()

    def _sync_action_button_states(self):
        super()._sync_action_button_states()
        if self._workspace_ready:
            self._refresh_workspace_status()

    def _refresh_workspace_status(self):
        self.build_button.setEnabled(bool(self.session.project.pages) and not self._generation_in_progress)
        for page in self.session.project.pages:
            fields = self._page_fields.get(page['id'])
            if fields is None:
                continue
            prompt = page['prompt']
            fields['status'].setText('Settings changed · rebuild when ready' if prompt['stale'] and prompt['text'] else '')
            fields['copy'].setEnabled(bool(prompt['text']))
            fields['send'].setEnabled(bool(prompt['text']) and not self._site_request_in_progress and not self._generation_in_progress)

    def _confirm_replacement(self, batch):
        if not any(item.replaces for item in batch.items):
            return True
        dialog = LetterSmithConfirmationDialog(self, title='Rebuild prompts',
            question='Replace the current prompt text with new prompts from these instructions?',
            primary_text='Build Prompts', secondary_text='Cancel')
        return dialog.exec() == QtWidgets.QDialog.Accepted

    def _show_batch_results(self, batch):
        failed = [item for item in batch.items if item.status != 'success']
        self.workspace_status.setText('\n'.join(f'{item.label}: {item.detail}' for item in failed)
                                      if failed else 'Prompts ready. Edit, copy, or create an image for any page.')
        self._refresh_workspace_status()
        if not failed:
            self._preview_scroll.ensureWidgetVisible(self.output_heading, 0, 20)

    def attach_browser(self, browser, image_tab=None):
        super().attach_browser(browser, image_tab)
        self.service_button.setText(browser.provider or 'Image Service')
        self._bind_image_target(self._view_page_id)

    def _bind_image_target(self, page_id):
        if self._browser is None or page_id is None:
            return
        # Each generation gets an immutable route. Later selection changes do
        # not relabel buttons already attached to a generated image.
        token = uuid.uuid4().hex
        self._image_destination = page_id
        with QtCore.QSignalBlocker(self.destination_selector):
            self.destination_selector.setCurrentIndex(self.destination_selector.findData(page_id))
        self._browser_targets[token] = self.session.capture_target(page_id, fields=('type',), include_shared=False)
        if hasattr(self._browser, 'set_approval_target'):
            self._browser.set_approval_target(token, page_label(self.session.project, page_id))
        self.workspace_status.setText('Images will be placed in ' + page_label(self.session.project, page_id) + '.')

    def _open_service(self):
        if self._browser is None:
            self.provider_requested.emit()
        if self._browser is not None:
            self._show_conversation()

    def _send_page(self, page_id):
        if self._browser is None:
            self.provider_requested.emit()
        if self._browser is not None:
            self._generate_with_provider(page_id)

    def _generate_with_provider(self, page_id):
        if self._browser is None or self._site_request_in_progress:
            return
        prompt = self.session.project.get_prompt(page_id)['text']
        if not prompt:
            return
        self._bind_image_target(page_id)
        self._site_request_in_progress = True
        self._site_status.setText('Sending prompt to ' + self._browser.provider + '…')
        self._site_status.show()
        self._show_conversation()
        self._sync_action_button_states()
        self._browser.submit_prompt(prompt, self._on_provider_submission)

    def _on_provider_submission(self, ok, message):
        self._site_request_in_progress = False
        self._site_status.setText(message + (' Choose “Use image for…” beneath the finished image.' if ok else ''))
        self._site_status.show()
        self._sync_action_button_states()

    def _on_browser_image_ready(self, token, path):
        target = self._browser_targets.get(token)
        if target is None or not self.session.target_is_current(target):
            self._on_browser_image_failed('That image belongs to a page or project that has changed. Select its destination again.')
            return
        if self._image_tab is None or self._pending_approval_key:
            self._on_browser_image_failed('Finish the current image before choosing another.')
            return
        self._pending_approval_key = target.page_id
        try:
            started = self._image_tab.import_image(target.page_id, path, target=target)
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            started = False
            self._on_browser_image_failed(str(error))
        if not started:
            self._pending_approval_key = ''
            self._on_browser_image_failed('The image could not be saved to its page.')

    @property
    def is_open(self):
        stack = getattr(self._host, 'application_stack', None)
        return not self._shutdown and (stack.currentWidget() is self if stack is not None else self.isVisible())

    def popup(self, launcher=None):
        if self._shutdown:
            return
        self.reload_project_state()
        if not any(self.session.target_is_current(target) for target in self._browser_targets.values()):
            self._bind_image_target(self._view_page_id)
        self._output_stack.setCurrentWidget(self._preview_scroll)
        stack = getattr(self._host, 'application_stack', None)
        if stack is not None:
            if stack.indexOf(self) < 0:
                stack.addWidget(self)
            stack.setCurrentWidget(self)
        else:
            self.showMaximized()
        fields = self._page_fields.get(self._view_page_id)
        if fields:
            QtCore.QTimer.singleShot(0, lambda: self._preview_scroll.ensureWidgetVisible(fields['directions_card'], 0, 24))

    open_with_anim = popup

    def _on_close(self):
        if not self.persist_project_state():
            self.workspace_status.setText('The prompt draft could not be saved. Keep this workspace open and retry.')
            return False
        self.popdown()
        self.dismissed.emit()
        return True

    close_with_anim = _on_close

    def popdown(self):
        stack = getattr(self._host, 'application_stack', None)
        if stack is not None and stack.currentWidget() is self:
            stack.setCurrentWidget(self._host.body)
        else:
            self.hide()

    def closeEvent(self, event):
        if self._shutdown or self._on_close():
            event.accept()
        else:
            event.ignore()
