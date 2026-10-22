"""Full-page presentation of the existing, canonical Prompt Writer controller."""
import uuid

from PySide6 import QtCore, QtGui, QtWidgets

from PromptWriterPanel import PromptWriterPanel as _PromptWriterPanel, PageSpec
from project_model import PAGE_TYPES
from prompt_writer_project import resolve_page_prompt, page_label
from ui_dialogs import LetterSmithConfirmationDialog
from prompt_writer_colors import (category_colors, display_color, ensure_page_colors,
                                  surface_colors, surface_stylesheet)


class _FlowLayout(QtWidgets.QLayout):
    """Content-sized controls with height calculated from the available width."""
    def __init__(self, parent=None, spacing=12):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return QtCore.Qt.Orientations()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QtCore.QRect(0, 0, width, 0), measure=True)

    def minimumSize(self):
        size = QtCore.QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QtCore.QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def sizeHint(self):
        return self.minimumSize()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect)

    def _arrange(self, rect, measure=False):
        margins = self.contentsMargins()
        area = rect.marginsRemoved(margins)
        x, y, row_height = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            size = item.sizeHint().expandedTo(item.minimumSize()).boundedTo(item.maximumSize())
            size.setWidth(min(size.width(), max(0, area.width())))
            if x > area.x() and x + size.width() > area.right() + 1:
                x, y, row_height = area.x(), y + row_height + self.spacing(), 0
            if not measure:
                item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), size))
            x += size.width() + self.spacing()
            row_height = max(row_height, size.height())
        return y + row_height - rect.y() + margins.bottom()


class _PagePromptCard(QtWidgets.QPushButton):
    def __init__(self, page_id, session_id):
        super().__init__()
        self.page_id, self.session_id = page_id, session_id
        self._press_position = None
        self._dragging = False
        self.setProperty('letterSmithPageCard', True)
        self.setCheckable(True)
        self.setFixedSize(158, 68)
        self.setCursor(QtCore.Qt.OpenHandCursor)

    def mousePressEvent(self, event):
        self._dragging = False
        self._press_position = event.position().toPoint() if event.button() == QtCore.Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self.isEnabled() and self._press_position is not None and event.buttons() & QtCore.Qt.LeftButton
                and (event.position().toPoint() - self._press_position).manhattanLength()
                >= QtWidgets.QApplication.startDragDistance()):
            self._dragging = True
            self.setDown(False)
            self._start_drag()
            self._press_position = None
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_position = None
        if self._dragging:
            self._dragging = False
            self.setDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _start_drag(self):
        from prompt_writer_browser import PAGE_CARD_MIME
        mime = QtCore.QMimeData()
        mime.setData(PAGE_CARD_MIME, self.page_id.encode('utf-8'))
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(self._press_position or self.rect().center())
        try:
            drag.exec(QtCore.Qt.CopyAction)
        finally:
            drag.deleteLater()


class _CollapsibleSection(QtWidgets.QWidget):
    """Presentation-only collapse state; editors remain connected while hidden."""
    def __init__(self, title, *, preview=False):
        super().__init__()
        self._preview = preview
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        header = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel(title)
        self.title.setWordWrap(True)
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        header.addWidget(self.title)
        self.summary = QtWidgets.QLabel()
        self.summary.setTextFormat(QtCore.Qt.PlainText)
        self.summary.setWordWrap(True)
        header.addWidget(self.summary, 1)
        header.addStretch()
        self.toggle = QtWidgets.QPushButton()
        self.toggle.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.toggle.clicked.connect(lambda: self.set_expanded(not self.expanded))
        header.addWidget(self.toggle)
        layout.addLayout(header)
        self.body = QtWidgets.QWidget()
        self.body_layout = QtWidgets.QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(8)
        layout.addWidget(self.body)
        self.set_expanded(not preview)

    def set_expanded(self, expanded):
        self.expanded = expanded
        self.body.setVisible(expanded)
        self.summary.setVisible(not expanded and not self._preview)
        self.toggle.setText(('Hide' if expanded else 'View') if self._preview
                            else ('Finish Selecting' if expanded else 'Edit'))
        self.toggle.setAccessibleName(self.toggle.text() + ' — ' + self.title.text())


class PromptWriterPanel(_PromptWriterPanel):
    def __init__(self, parent=None, **kwargs):
        self._workspace_ready = False
        self._page_fields = {}
        self._fields_session = None
        self._image_destination = None
        self._image_target_token = None
        self._image_target_browser = None
        self._image_target_label = ''
        self._service_buttons = {}
        self._handoff_serial = 0
        self._card_drop_serial = 0
        self._introduced_services = set()
        self._live_refreshing = False
        self._prompt_errors = {}
        self._semantic_controls = []
        self._page_colors = {}
        self.manage_buttons = {}
        self._host = parent
        super().__init__(parent, **kwargs)
        self._visionary_timer.stop()
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, False)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, False)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        if getattr(parent, 'application_stack', None) is not None:
            self.setWindowFlags(QtCore.Qt.Widget)
            self.title_bar.window_controller.shutdown()
        self.setMinimumSize(480, 350)
        self._workspace_ready = True
        self.reload_project_state()

    def _build_ui(self):
        # Reuse the established inputs, libraries and canonical controller.
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
        self.prompts_button = QtWidgets.QPushButton('‹ Prompt Writer')
        self.prompts_button.clicked.connect(self._show_writer)
        self.prompts_button.hide()
        self.service_button = QtWidgets.QPushButton('Create Image')
        self.service_button.clicked.connect(self._open_service)
        heading.addWidget(self.prompts_button)
        heading.addWidget(self.service_button)
        root.addLayout(heading)
        self.workspace_status = QtWidgets.QLabel()
        self.workspace_status.setWordWrap(True)
        self.workspace_status.setTextFormat(QtCore.Qt.PlainText)
        root.addWidget(self.workspace_status)
        self.workspace_status.hide()

        self._preview_scroll = QtWidgets.QScrollArea()
        self._preview_scroll.setWidgetResizable(True)
        self._preview_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._preview_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        content = QtWidgets.QWidget()
        content.setObjectName('WriterContent')
        content.setProperty('themeIndependent', True)
        content.setStyleSheet(surface_stylesheet())
        form = QtWidgets.QVBoxLayout(content)
        form.setContentsMargins(2, 2, 14, 12)
        form.setSpacing(12)
        self.target_selector.hide()
        self.generated_section = _CollapsibleSection('Generated Prompts', preview=True)
        self.output_heading = self.generated_section.title
        self.outputs_container = QtWidgets.QWidget()
        self.outputs_layout = QtWidgets.QVBoxLayout(self.outputs_container)
        self.outputs_layout.setContentsMargins(0, 0, 0, 0)
        self.outputs_layout.setSpacing(16)
        self.generated_section.body_layout.addWidget(self.outputs_container)
        form.addWidget(self.generated_section)
        self.style_section = _CollapsibleSection('Project Style')
        self.guidance_section = _CollapsibleSection('Optional Guidance')
        self.directions_section = _CollapsibleSection('Directions')
        self.editing_sections = (self.style_section, self.guidance_section, self.directions_section)
        shared_form = _FlowLayout(spacing=16)
        self.style_section.body_layout.addLayout(shared_form)
        for key, semantic_key, label, combo in (
                ('subject', 'subject', self.lbl_subject, self.cmb_subject),
                ('type', 'type', self.lbl_type, self.cmb_type),
                ('color', 'scheme', self.lbl_color, self.cmb_color)):
            field = QtWidgets.QWidget()
            field.setFixedWidth(224)
            column = QtWidgets.QVBoxLayout(field)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(4)
            caption = QtWidgets.QHBoxLayout()
            caption.addWidget(label)
            caption.addStretch()
            manage = getattr(self, f'btn_manage_{key}')
            self.manage_buttons[key] = manage
            caption.addWidget(manage)
            column.addLayout(caption)
            combo.setMinimumSize(100, 32)
            combo.setMaximumSize(16777215, 32)
            combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(15)
            combo.setAccessibleName(label.text())
            combo.lineEdit().setPlaceholderText('None')
            column.addWidget(combo)
            shared_form.addWidget(field)
            self._semantic_controls.append((combo, semantic_key))
            self._semantic_controls.append((manage, semantic_key))
            label.show()
            combo.show()
        form.addWidget(self.style_section)
        self.guidance_layout = options = _FlowLayout(spacing=10)
        self.guidance_groups = []
        for title, identity, semantic_key, checkboxes in (
                ('Edges', 'edges', 'helpful', (self.cb_black, self.cb_white, self.cb_frame,
                                                  self.cb_vignette, self.cb_polaroid, self.cb_cardshadow)),
                ('Style Bias', 'style_bias', 'style_bias', (self.cb_real, self.cb_paint, self.cb_minimal)),
                ('Image Output', 'image_output', 'clean_output', (self.cb_forbid, self.cb_clean_composition, self.cb_simplified_details)),
                ('Camera Angle & Framing', 'camera_angle_framing', 'framing', (self.cb_strong_focal_point, self.cb_dynamic_angle,
                                                 self.cb_cinematic_framing, self.cb_close_up_focus,
                                                 self.cb_full_body_view, self.cb_wide_scene))):
            group = QtWidgets.QGroupBox(title.replace('&', '&&'))
            group.setObjectName('guidance_' + identity)
            group.setProperty('promptCategory', semantic_key)
            group.setAccessibleName(title)
            column = QtWidgets.QVBoxLayout(group)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(4)
            for checkbox in checkboxes:
                checkbox.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
                checkbox.setProperty('promptCategory', semantic_key)
                column.addWidget(checkbox)
                self._semantic_controls.append((checkbox, semantic_key))
                checkbox.show()
            options.addWidget(group)
            self.guidance_groups.append(group)
            self._semantic_controls.append((group, semantic_key))
        self.guidance_section.body_layout.addLayout(options)
        form.addWidget(self.guidance_section)
        self.lbl_global.setText('For All Pages')
        self.directions_section.body_layout.addWidget(self.lbl_global)
        self.lbl_global.show()
        self.txt_global.setMinimumHeight(75)
        self.txt_global.setMaximumHeight(120)
        self.txt_global.setAccessibleName('For All Pages')
        self.directions_section.body_layout.addWidget(self.txt_global)
        self._semantic_controls.append((self.txt_global, 'global'))
        self.txt_global.show()
        self.directions_container = QtWidgets.QWidget()
        self.directions_grid = QtWidgets.QGridLayout(self.directions_container)
        self.directions_grid.setContentsMargins(0, 0, 0, 0)
        self.directions_grid.setSpacing(16)
        self.directions_section.body_layout.addWidget(self.directions_container)
        form.addWidget(self.directions_section)
        form.addStretch()
        self._preview_scroll.setWidget(content)
        self._output_stack = QtWidgets.QStackedWidget()
        self._output_stack.addWidget(self._preview_scroll)
        self._service_empty = QtWidgets.QWidget()
        self._output_stack.addWidget(self._service_empty)
        self.conversation_bar = QtWidgets.QWidget()
        self.conversation_bar.setProperty('themeIndependent', True)
        self.conversation_bar.setStyleSheet(surface_stylesheet())
        service_layout = QtWidgets.QVBoxLayout(self.conversation_bar)
        service_layout.setContentsMargins(0, 0, 0, 0)
        service_layout.setSpacing(4)
        self.service_destination_label = QtWidgets.QLabel()
        self.service_destination_label.setWordWrap(True)
        service_layout.addWidget(self.service_destination_label)
        self.service_targets_layout = _FlowLayout(spacing=6)
        service_layout.addLayout(self.service_targets_layout)
        self.destination_selector = QtWidgets.QComboBox()
        self.destination_selector.setAccessibleName('Destination for new conversation images')
        self.destination_selector.setParent(self.conversation_bar)
        self.destination_selector.hide()
        self.destination_selector.activated.connect(lambda: self._activate_service_page(self.destination_selector.currentData()))
        self.service_prompt = QtWidgets.QPlainTextEdit()
        self.service_prompt.setReadOnly(True)
        self.service_prompt.setAccessibleName('Ready when you are — current page prompt')
        self.service_prompt.setFixedHeight(76)
        self.service_prompt.setParent(self.conversation_bar)
        self.service_prompt.hide()
        self.handoff_status = QtWidgets.QLabel()
        self.handoff_status.setWordWrap(True)
        self.handoff_status.hide()
        service_layout.addWidget(self.handoff_status)
        self._handoff_timer = QtCore.QTimer(self)
        self._handoff_timer.setSingleShot(True)
        self._handoff_timer.timeout.connect(self.handoff_status.hide)
        root.addWidget(self.conversation_bar)
        self.conversation_bar.hide()
        self._output_stack.currentChanged.connect(self._workspace_view_changed)
        root.addWidget(self._output_stack, 1)
        # Existing async import/submission handlers already report here.
        root.addWidget(self._site_status)
        self._site_status.setWordWrap(True)

    def _apply_styles(self):
        tokens = getattr(self, '_theme_tokens', None)
        for surface in (getattr(self, '_preview_scroll', None), getattr(self, 'conversation_bar', None)):
            if isinstance(surface, QtWidgets.QScrollArea):
                surface = surface.widget()
            if surface is not None:
                surface.setStyleSheet(surface_stylesheet(tokens))
        background = getattr(tokens, 'background', self.palette().window().color().name())
        text = getattr(tokens, 'text', self.palette().windowText().color().name())
        border = getattr(tokens, 'border', self.palette().mid().color().name())
        self.setStyleSheet(f'QWidget#PromptWriterPanel, QWidget#WriterContent {{ background:{background}; color:{text}; }}'
                          f'QGroupBox {{ color:{text}; border-color:{border}; }}' + '''
            QScrollArea { border: none; background: transparent; }
            QGroupBox { border: 1px solid palette(mid); border-radius: 6px;
                        margin-top: 10px; padding: 14px 10px 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPlainTextEdit, QTextEdit { padding: 8px; }
            QPushButton { min-height: 26px; padding: 4px 12px; }
            QToolButton { padding: 3px 6px; }
            QCheckBox { spacing: 7px; padding: 2px 0; }
        ''')

    def _display_prompt_color(self, semantic_key):
        if semantic_key in category_colors():
            return display_color(category_colors()[semantic_key], self._theme_tokens)
        return super()._display_prompt_color(semantic_key)

    def _page_prompt_color(self, page_id, fallback='cover'):
        color = self._page_colors.get(page_id, super()._page_prompt_color(page_id, fallback))
        return display_color(color, self._theme_tokens)

    @staticmethod
    def _color_icon(color):
        pixmap = QtGui.QPixmap(12, 12)
        pixmap.fill(QtGui.QColor(color))
        return QtGui.QIcon(pixmap)

    def _color_page_selectors(self):
        palette = surface_colors(self._theme_tokens)
        for combo in (self.target_selector, self.destination_selector):
            combo.setProperty('themeIndependent', True)
            for index in range(combo.count()):
                color = self._page_prompt_color(combo.itemData(index))
                if color:
                    combo.setItemData(index, QtGui.QColor(color), QtCore.Qt.ForegroundRole)
                    combo.setItemIcon(index, self._color_icon(color))
            color = self._page_prompt_color(combo.currentData()) if combo.currentData() else palette['text']
            combo.setProperty('promptColor', color)
            combo.setStyleSheet(f"QComboBox {{ color:{color}; background:{palette['input']}; }}")
            combo.view().setStyleSheet(surface_stylesheet(self._theme_tokens))

    def _style_managers(self):
        for key, dialog in self._list_manager_dialogs.items():
            color = self._display_prompt_color('scheme' if key == 'color' else key)
            dialog.setProperty('themeIndependent', True)
            dialog.setStyleSheet(surface_stylesheet(self._theme_tokens) + f'''
                QLabel#managerTitle, QLineEdit, QListWidget, QListWidget QLabel {{ color:{color}; }}
                QLabel#managerTitle {{ font-size:15px; font-weight:700; }}
            ''')
            dialog.entry_edit.setProperty('promptColor', color)
            dialog._header_color = QtGui.QColor(color)
            for index in range(dialog.list_widget.count()):
                dialog.list_widget.item(index).setForeground(QtGui.QColor(color))

    def _open_list_manager(self, key, *, for_page=False):
        super()._open_list_manager(key, for_page=for_page)
        self._style_managers()

    def refresh_semantic_palette(self):
        super().refresh_semantic_palette()
        palette = surface_colors(self._theme_tokens)
        for control, key in self._semantic_controls:
            color = self._display_prompt_color(key)
            control.setProperty('themeIndependent', True)
            control.setProperty('promptColor', color)
            control.setStyleSheet(f'color:{color};')
            if isinstance(control, QtWidgets.QComboBox):
                control.view().setProperty('themeIndependent', True)
                control.view().setStyleSheet(surface_stylesheet(self._theme_tokens) +
                    f'QAbstractItemView {{ color:{color}; selection-color:{color}; }}')
                delegate = control.itemDelegate()
                if hasattr(delegate, '_header_color'):
                    delegate._header_color = QtGui.QColor(color)
                # Decorating the current model item can reset an editable combo's draft.
                with QtCore.QSignalBlocker(control):
                    current_text = control.currentText()
                    for index in range(control.count()):
                        control.setItemData(index, QtGui.QColor(color), QtCore.Qt.ForegroundRole)
                    if control.isEditable():
                        control.setEditText(current_text)
            control.update()
        if self._workspace_ready:
            for page in self.session.project.pages:
                fields = self._page_fields.get(page['id'])
                if fields:
                    key = PAGE_TYPES[page['type']].artwork_template
                    color = self._page_prompt_color(page['id'], key)
                    for card in (fields['directions_card'], fields['output_card']):
                        card.setProperty('themeIndependent', True)
                        card.setStyleSheet(f'QGroupBox {{ border-color:{color}; }} QGroupBox::title {{ color:{color}; }}')
                    fields['directions'].setStyleSheet(f'color:{color};')
                    fields['output'].setProperty('themeIndependent', True)
                    fields['output'].setStyleSheet(f"color:{palette['text']}; background:{palette['input']};")
                    spec = PageSpec(page['id'], page_label(self.session.project, page['id']), '', key, '', '',
                                    detail_widget=fields['directions'], preview_widget=fields['output'])
                    self._highlight_saved_prompt(spec)
            self._color_page_selectors()
            self._style_managers()
            for page_id, button in self._service_buttons.items():
                color = self._page_prompt_color(page_id)
                button.setProperty('promptColor', color)
                button.setStyleSheet(f'QPushButton {{ color:{color}; border:1px solid {color}; border-radius:8px; padding:6px; }}'
                                    f"QPushButton:hover, QPushButton:checked {{ background:{palette['selected']}; border-width:2px; }}")

    def _persist_state_now(self):
        saved = super()._persist_state_now()
        if not saved and self._workspace_ready:
            self._set_workspace_status('Save failed. Your prompt edits are still open; retry before leaving.')
        return saved

    def reload_project_state(self):
        if not self.session.is_restoring:
            revision = self.session.project.revision
            was_writing = self._writing
            self._writing = True
            try:
                self._page_colors = ensure_page_colors(self.session.project)
            finally:
                self._writing = was_writing
            if self.session.project.revision != revision:
                self._schedule_persist_state()
            # Correct the old label without rewriting historical prompt text.
            self._writing = True
            try:
                shared = self.session.project.to_dict()['prompt_settings']
                if shared.get('subject', '').casefold() == 'discouragement':
                    self.session.project.update_project_data('prompt_settings', {'subject': 'Encouragement'})
                for page in self.session.project.pages:
                    overrides = page['prompt']['overrides']
                    if overrides.get('subject', '').casefold() == 'discouragement':
                        self.session.project.update_prompt(page['id'], {'overrides': {**overrides, 'subject': 'Encouragement'}})
            finally:
                self._writing = False
        result = super().reload_project_state()
        if result and self._workspace_ready:
            self._refresh_live_prompts(update_editors=False)
            self._refresh_pages()
        return result

    def _refresh_pages(self):
        pages = self.session.project.pages
        if (self._fields_session != self.session.session_id
                or list(self._service_buttons) != [page['id'] for page in pages]):
            while self.service_targets_layout.count():
                item = self.service_targets_layout.takeAt(0)
                item.widget().hide()
                item.widget().deleteLater()
            self._service_buttons = {}
            for page in pages:
                page_id = page['id']
                button = _PagePromptCard(page_id, self.session.session_id)
                button.clicked.connect(lambda checked=False, key=page_id: self._activate_service_page(key))
                self.service_targets_layout.addWidget(button)
                self._service_buttons[page_id] = button
        with QtCore.QSignalBlocker(self.destination_selector):
            self.destination_selector.clear()
            for page in pages:
                self.destination_selector.addItem(page_label(self.session.project, page['id']), page['id'])
            self.destination_selector.setCurrentIndex(self.destination_selector.findData(self._image_destination or self._view_page_id))
        ids = [p['id'] for p in pages]
        new_session = self._fields_session != self.session.session_id
        if new_session:
            self._handoff_serial += 1
            self._site_request_in_progress = False
            self._introduced_services.clear()
            self.generated_section.set_expanded(False)
            for section in self.editing_sections:
                section.set_expanded(True)
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
                directions.setMinimumHeight(90)
                directions.setMaximumHeight(160)
                directions.setPlaceholderText('Describe the scene and composition for this page.')
                direction_layout.addWidget(directions)
                output_card = QtWidgets.QGroupBox()
                output_layout = QtWidgets.QVBoxLayout(output_card)
                output = QtWidgets.QPlainTextEdit()
                output.setMinimumHeight(230)
                output.setMaximumHeight(360)
                output.setPlaceholderText('The prompt updates automatically from this page’s settings.')
                output_layout.addWidget(output)
                actions = QtWidgets.QHBoxLayout()
                status = QtWidgets.QLabel()
                status.setWordWrap(True)
                actions.addWidget(status, 1)
                copy = QtWidgets.QPushButton('Copy')
                copy.clicked.connect(lambda checked=False, key=page_id: self._copy_prompt(key))
                actions.addWidget(copy)
                output_layout.addLayout(actions)
                self.outputs_layout.addWidget(output_card)
                fields = dict(directions_card=directions_card, directions=directions,
                              output_card=output_card, output=output, status=status, copy=copy)
                self._page_fields[page_id] = fields
                for key, editor in (('directions', directions), ('text', output)):
                    editor.textChanged.connect(lambda pid=page_id, field=key, widget=editor, sid=self._fields_session:
                                               self._edit_page(pid, field, widget, sid))
        for index, page in enumerate(pages, 1):
            fields = self._page_fields[page['id']]
            kind = PAGE_TYPES[page['type']].label
            label = f'Page {index} — {kind}'
            if page['name'] and page['name'] != kind:
                label += ' · ' + page['name']
            fields['directions_card'].setTitle(label.replace('&', '&&'))
            fields['output_card'].setTitle(label.replace('&', '&&'))
            for key, editor in (('directions', fields['directions']), ('text', fields['output'])):
                with QtCore.QSignalBlocker(editor):
                    self._restore_text(editor, page['prompt'][key] or '', new_session)
                editor.setAccessibleName(label + (' directions' if key == 'directions' else ' prompt'))
        self._arrange_directions()
        self._refresh_workspace_status()
        self.refresh_semantic_palette()
        self._refresh_service_state()

    def _arrange_directions(self):
        for index, fields in enumerate(self._page_fields.values()):
            self.directions_grid.addWidget(fields['directions_card'], index, 0)
            fields['directions_card'].show()
            fields['output_card'].show()
        self.directions_grid.setColumnStretch(0, 1)

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
        if key == 'directions':
            self._refresh_live_prompts([page_id])
        else:
            self.refresh_semantic_palette()

    def _on_prompt_input_changed(self, *args):
        super()._on_prompt_input_changed(*args)
        if self._workspace_ready and not self._state_persistence_suspended:
            self._refresh_live_prompts()

    def _refresh_live_prompts(self, page_ids=None, *, update_editors=True):
        if self._live_refreshing or self._generation_in_progress or self.session.is_restoring:
            return
        project = self.session.project
        ids = set(page_ids) if page_ids is not None else {page['id'] for page in project.pages}
        revision = project.revision
        was_writing = self._writing
        self._live_refreshing = self._writing = True
        self._prompt_errors = {key: error for key, error in self._prompt_errors.items()
                               if key not in ids and key in {page['id'] for page in project.pages}}
        try:
            for page in project.pages:
                if page['id'] not in ids:
                    continue
                try:
                    text = resolve_page_prompt(project, page['id'], self._roll_shared_prompt_data,
                                               self._guidance_phrases())
                    fields = self._page_fields.get(page['id'])
                    if update_editors and fields and self._fields_session == self.session.session_id:
                        self._replace_plain_text_as_one_edit(fields['output'], text)
                except (ValueError, KeyError) as error:
                    self._prompt_errors[page['id']] = str(error)
        finally:
            self._live_refreshing = False
            self._writing = was_writing
        self._set_workspace_status('\n'.join(
            f"{page_label(project, key)}: {error}" for key, error in self._prompt_errors.items()))
        self._refresh_status()
        self.refresh_semantic_palette()
        if project.revision != revision:
            self._schedule_persist_state()
        # Reload rebuilds page controls next; old buttons can reference deleted pages.
        if update_editors:
            self._refresh_service_state()

    def current_page_prompt(self, page_id=None):
        """Authoritative plain-text accessor for preview, copy and image handoff.

        Construction is synchronous, so returning flushes all relevant inputs.
        Historical/manual wording is resolved by the same service as live edits.
        """
        page_id = page_id if page_id is not None else self._view_page_id
        if page_id is None:
            return ''
        if self.session.is_restoring or self._generation_in_progress:
            raise RuntimeError('The page prompt is being updated. Try again when it is ready.')
        self.session.project.get_page(page_id)
        self._refresh_live_prompts([page_id])
        if page_id in self._prompt_errors:
            raise ValueError(self._prompt_errors[page_id])
        return self.session.project.get_prompt(page_id)['text'] or ''

    def _copy_prompt(self, page_identifier):
        try:
            text = self.current_page_prompt(page_identifier)
            QtWidgets.QApplication.clipboard().setText(text)
            return text
        except (KeyError, ValueError, RuntimeError, OSError) as error:
            self._set_workspace_status(str(error))
            return ''

    def _copy_all_prompts_text(self):
        return self._copy_prompt(self._view_page_id)

    def _page_input_changed(self, *args):
        super()._page_input_changed(*args)
        if self._workspace_ready and not self._state_persistence_suspended:
            self._refresh_live_prompts([self._view_page_id])

    def _output_changed(self, *args):
        super()._output_changed(*args)
        if self._workspace_ready and not self._state_persistence_suspended:
            self._refresh_live_prompts()

    def _refresh_status(self):
        super()._refresh_status()
        if self._workspace_ready:
            self._refresh_workspace_status()

    def _sync_action_button_states(self):
        super()._sync_action_button_states()
        if self._workspace_ready:
            self._refresh_workspace_status()

    def _refresh_workspace_status(self):
        self.service_button.setEnabled(bool(self.session.project.pages) and not self.session.is_restoring)
        for button in self._service_buttons.values():
            button.setEnabled(not self._site_request_in_progress)
        self._refresh_section_summaries()
        for page in self.session.project.pages:
            fields = self._page_fields.get(page['id'])
            if fields is None:
                continue
            prompt = page['prompt']
            baseline = prompt['resolved_components'].get('generated_text')
            status = ('Saved wording retained' if prompt['stale'] and baseline is None and prompt['text']
                      else 'Custom wording retained' if baseline is not None and prompt['text'] != baseline else '')
            fields['status'].setText(status)
            ready = bool(prompt['text']) and page['id'] not in self._prompt_errors
            fields['copy'].setEnabled(ready)

    def _set_workspace_status(self, text):
        self.workspace_status.setText(text)
        self.workspace_status.setVisible(bool(text))

    def _refresh_section_summaries(self):
        shared = self.session.project.to_dict()['prompt_settings']
        choices = [(label, str(shared.get(key) or '').strip()) for key, label in
                   (('subject', 'Subject'), ('type', 'Illustration'), ('color', 'Color Scheme'))]
        # Bound summaries so long custom text cannot turn a collapsed section into a panel.
        def compact(value):
            value = ' '.join(value.split())
            return value if len(value) <= 48 else value[:45] + '…'
        self.style_section.summary.setText(' · '.join(
            f'{label}: {compact(value) if value else "None"}' for label, value in choices)
            if any(value for _, value in choices) else 'No custom selections.')
        selected = [checkbox.text().replace('&&', '&') for checkbox, key in self._checkbox_state_specs()
                    if shared.get('checks', {}).get(key)]
        self.guidance_section.summary.setText(
            compact(' · '.join(selected)) if selected else 'No custom selections.')
        directed = sum(bool(page['prompt']['directions'].strip()) for page in self.session.project.pages)
        directions = ['For All Pages'] if str(shared.get('global') or '').strip() else []
        if directed:
            directions.append(f'{directed} page' + ('s' if directed != 1 else '') + ' with directions')
        self.directions_section.summary.setText(' · '.join(directions) if directions else 'No custom selections.')

    def _confirm_replacement(self, batch):
        if not any(item.replaces for item in batch.items):
            return True
        dialog = LetterSmithConfirmationDialog(self, title='Rebuild prompts',
            question='Replace the current prompt text with new prompts from these instructions?',
            primary_text='Build Prompts', secondary_text='Cancel')
        return dialog.exec() == QtWidgets.QDialog.Accepted

    def _show_batch_results(self, batch):
        failed = [item for item in batch.items if item.status != 'success']
        self._set_workspace_status('\n'.join(f'{item.label}: {item.detail}' for item in failed)
                                      if failed else 'Prompts ready. Edit, copy, or create an image for any page.')
        self._refresh_workspace_status()
        if not failed:
            self._preview_scroll.ensureWidgetVisible(self.output_heading, 0, 20)

    def attach_browser(self, browser, image_tab=None):
        if self._browser is not None and hasattr(self._browser, 'pageCardDropped'):
            try:
                self._browser.pageCardDropped.disconnect(self._on_page_card_dropped)
            except (RuntimeError, TypeError):
                pass
        super().attach_browser(browser, image_tab)
        if hasattr(browser, 'pageCardDropped'):
            browser.pageCardDropped.connect(self._on_page_card_dropped)
        self._bind_image_target(self._view_page_id)

    def _on_page_card_dropped(self, card, position):
        page_id = getattr(card, 'page_id', None)
        session_id = self.session.session_id
        browser = self._browser
        if (self._shutdown or self._site_request_in_progress or browser is None
                or getattr(card, 'session_id', None) != session_id
                or self._service_buttons.get(page_id) is not card
                or self._output_stack.currentWidget() is not browser.view):
            return
        self._card_drop_serial += 1
        serial = self._card_drop_serial
        def checked(inside):
            if (self._shutdown or serial != self._card_drop_serial or self.session.session_id != session_id
                    or browser is not self._browser or self._site_request_in_progress
                    or self._service_buttons.get(page_id) is not card
                    or self._output_stack.currentWidget() is not browser.view):
                return
            if inside:
                self._activate_service_page(page_id)
            else:
                self._show_handoff_status('Drop the card inside the message box to send its prompt.')
        browser.is_composer_at(position, checked)

    def _workspace_view_changed(self):
        service_visible = self._output_stack.currentWidget() is not self._preview_scroll
        self.conversation_bar.setVisible(service_visible)
        self.prompts_button.setVisible(service_visible)
        self.service_button.setVisible(not service_visible)

    def _show_writer(self):
        self._card_drop_serial += 1
        self.generated_section.set_expanded(False)
        self._output_stack.setCurrentWidget(self._preview_scroll)
        self._preview_scroll.verticalScrollBar().setValue(0)

    def _bind_image_target(self, page_id):
        if page_id is None:
            return
        # Each generation gets an immutable route. Later selection changes do
        # not relabel buttons already attached to a generated image.
        self._image_destination = page_id
        with QtCore.QSignalBlocker(self.destination_selector):
            self.destination_selector.setCurrentIndex(self.destination_selector.findData(page_id))
        self._color_page_selectors()
        if self._browser is None:
            return
        target = self._browser_targets.get(self._image_target_token)
        label = self._service_page_label(page_id)
        if (target is None or target.page_id != page_id or not self.session.target_is_current(target)
                or self._image_target_browser is not self._browser):
            self._image_target_token = uuid.uuid4().hex
            self._browser_targets[self._image_target_token] = self.session.capture_target(page_id, fields=('type',), include_shared=False)
            self._image_target_browser = self._browser
            self._image_target_label = ''
        if hasattr(self._browser, 'set_approval_target') and self._image_target_label != label:
            self._browser.set_approval_target(self._image_target_token, label)
            if hasattr(self._browser, 'set_approval_color'):
                self._browser.set_approval_color(self._image_target_token, self._page_prompt_color(page_id))
            self._image_target_label = label

    def _service_page_label(self, page_id):
        label = page_label(self.session.project, page_id)
        peers = [page['id'] for page in self.session.project.pages
                 if page_label(self.session.project, page['id']) == label]
        return label + f' ({peers.index(page_id) + 1})' if len(peers) > 1 else label

    def _refresh_service_state(self):
        if not self._workspace_ready or self.session.is_restoring:
            return
        page_id = self._view_page_id
        if page_id not in self._page_fields:
            return
        if self._image_destination != page_id:
            self._handoff_timer.stop()
            self.handoff_status.hide()
        self._bind_image_target(page_id)
        label = self._service_page_label(page_id)
        self.service_destination_label.setText('Images for ' + label)
        self.service_destination_label.setStyleSheet(f'color:{self._page_prompt_color(page_id)};')
        self.service_prompt.setAccessibleName('Ready when you are — ' + label)
        text = self.session.project.get_prompt(page_id)['text'] or ''
        if self.service_prompt.toPlainText() != text:
            self.service_prompt.setPlainText(text)
        page = self.session.project.get_page(page_id)
        self._highlight_saved_prompt(PageSpec(page_id, label, '', PAGE_TYPES[page['type']].artwork_template,
            '', '', detail_widget=self._page_fields[page_id]['directions'], preview_widget=self.service_prompt))
        for index, (identity, button) in enumerate(self._service_buttons.items(), 1):
            name = self._service_page_label(identity)
            short_name = button.fontMetrics().elidedText(name, QtCore.Qt.ElideRight, 138).replace('&', '&&')
            button.setText(f'⠿  Page {index}\n{short_name}')
            button.setToolTip(name + '\nDrag into the message box or click to send.')
            button.setAccessibleName(f'Page {index} — Generate image for {name}')
            button.setChecked(identity == page_id)

    def _show_handoff_status(self, message):
        self.handoff_status.setText(message)
        self.handoff_status.show()
        self._handoff_timer.start(6000)

    @staticmethod
    def _write_prompt_clipboard(text):
        clipboard = QtWidgets.QApplication.clipboard()
        clipboard.setText(text)
        if clipboard.text() != text:
            raise OSError('Clipboard did not retain the prompt.')

    def _activate_service_page(self, page_id):
        if self._site_request_in_progress:
            return
        if self._browser is None:
            self.provider_requested.emit()
        if self._browser is None:
            self._show_handoff_status('Choose an image service and sign in first.')
            return
        self._generate_with_provider(page_id)

    def _service_introduction_key(self):
        view = getattr(self._browser, 'view', None)
        url = view.url().toString() if callable(getattr(view, 'url', None)) else ''
        return (self.session.session_id, id(self._browser),
                getattr(self._browser, 'provider', ''), url)

    def _project_introduction(self):
        project = self.session.project
        shared = project.to_dict()['prompt_settings']
        lines = [f"I'm creating a {len(project.pages)}-page illustrated letter project in LetterSmith.", 'Pages:']
        for index, page in enumerate(project.pages, 1):
            kind = PAGE_TYPES[page['type']].label
            name = page_label(project, page['id'])
            lines.append(f'{index}. {kind}' + (f' — {name}' if name != kind else ''))
        for key, label in (('subject', 'Subject'), ('type', 'Illustration style'), ('color', 'Color scheme'),
                           ('global', 'Project directions')):
            value = str(shared.get(key) or '').strip()
            if value:
                lines.append(f'{label}: {value}')
        lines.extend([
            'Choose any unspecified creative details to suit the project.',
            'Maintain a cohesive artistic identity across all pages, while allowing each page its own creative direction.',
            "I'll send one page's image prompt at a time. Generate only the image requested by that prompt.",
            'For now, do not generate any images. Just confirm that you are ready.',
            'Are you ready?',
        ])
        return '\n'.join(lines)

    def _show_conversation(self):
        valid = [page['id'] for page in self.session.project.pages]
        selected = self.session.selected_page_id
        page_id = selected if selected in valid else self._view_page_id if self._view_page_id in valid else next(iter(valid), None)
        if page_id is not None:
            if self._view_page_id != page_id and not self.select_page(page_id):
                return
            self.current_page_prompt(page_id)
            self._refresh_service_state()
        super()._show_conversation()
        if self._browser is None:
            self._output_stack.setCurrentWidget(self._service_empty)

    def _open_service(self):
        if self._browser is None:
            self.provider_requested.emit()
        self._show_conversation()
        if self._browser is not None and self._service_introduction_key() not in self._introduced_services:
            self._submit_service_prompt(self._project_introduction(), introduction=True)

    def _send_page(self, page_id):
        if self._browser is None:
            self.provider_requested.emit()
        if self._browser is not None:
            self._generate_with_provider(page_id)

    def _generate_with_provider(self, page_id):
        if self._browser is None or self._site_request_in_progress:
            return
        try:
            prompt = self.current_page_prompt(page_id)
        except (KeyError, ValueError, RuntimeError) as error:
            self._set_workspace_status(str(error))
            return
        if not prompt:
            return
        if not self.select_page(page_id):
            return
        self._bind_image_target(page_id)
        self._show_conversation()
        self._submit_service_prompt(prompt)

    def _submit_service_prompt(self, prompt, *, introduction=False):
        if self._browser is None or self._site_request_in_progress:
            return
        self._site_request_in_progress = True
        self._handoff_serial += 1
        serial, session_id = self._handoff_serial, self.session.session_id
        browser = self._browser
        self._handoff_timer.stop()
        self.handoff_status.hide()
        self._site_status.setText('Sending project introduction…' if introduction
                                  else 'Sending prompt to ' + browser.provider + '…')
        self._site_status.show()
        self._sync_action_button_states()
        def completed(ok, message):
            if serial != self._handoff_serial or session_id != self.session.session_id or browser is not self._browser:
                return
            if introduction and ok:
                self._introduced_services.add(self._service_introduction_key())
            if introduction:
                self._site_request_in_progress = False
                self._site_status.setText('Project introduction sent. Choose a page when ready.' if ok else message)
                self._sync_action_button_states()
            else:
                self._on_provider_submission(ok, message)
        try:
            browser.submit_prompt(prompt, completed)
        except (OSError, RuntimeError) as error:
            completed(False, str(error))

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
        self._show_writer()
        stack = getattr(self._host, 'application_stack', None)
        if stack is not None:
            if stack.indexOf(self) < 0:
                stack.addWidget(self)
            stack.setCurrentWidget(self)
        else:
            self.showMaximized()
        self._preview_scroll.verticalScrollBar().setValue(0)

    open_with_anim = popup

    def _on_close(self):
        if not self.persist_project_state():
            self._set_workspace_status('The prompt draft could not be saved. Keep this workspace open and retry.')
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
