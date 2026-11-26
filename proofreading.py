"""Shared, local writing suggestions for plain and rich text editors."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from difflib import SequenceMatcher
import logging
import re
import weakref

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

from language_service import LanguageIssue, get_language_service, is_automatic_spelling_fix


LOGGER = logging.getLogger(__name__)
PROOFREADING_SELECTION_PROPERTY = int(QtGui.QTextFormat.UserProperty) + 239
_TOKENS = re.compile(r"\w+(?:['\u2019-]\w+)*|[^\w\s]|\s+", re.UNICODE)


def proofreading_chunks(source, baseline=""):
    """Yield bounded paragraphs with their original character offsets."""
    before = baseline.splitlines(keepends=True)
    after = source.splitlines(keepends=True)
    offsets = [0]
    for line in after:
        offsets.append(offsets[-1] + len(line))
    for operation, _i, _j, first, last in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if operation == "equal":
            continue
        for number in range(first, last):
            paragraph = after[number].rstrip("\r\n\u2028\u2029")
            start = offsets[number]
            while paragraph:
                length = min(6000, len(paragraph))
                if length < len(paragraph):
                    boundary = max(paragraph.rfind(" ", 0, length), paragraph.rfind("\t", 0, length))
                    if boundary > length // 2:
                        length = boundary + 1
                part, paragraph = paragraph[:length], paragraph[length:]
                if part.strip():
                    yield start, part
                start += length


def grammar_issues(start, original, corrected, protected):
    """Convert a local correction into editable suggestions."""
    source_tokens = list(_TOKENS.finditer(original))
    target_tokens = list(_TOKENS.finditer(corrected))
    source = [item.group() for item in source_tokens]
    target = [item.group() for item in target_tokens]
    issues = []
    for operation, first, last, target_first, target_last in SequenceMatcher(
            None, source, target, autojunk=False).get_opcodes():
        if operation == "equal":
            continue
        left = source_tokens[first].start() if first < len(source_tokens) else len(original)
        right = source_tokens[last - 1].end() if last > first else left
        if any(left < end and right > begin or left == right and begin < left < end
               for begin, end in protected):
            continue
        replacement = "".join(target[target_first:target_last])
        issues.append(LanguageIssue(start + left, start + right, original[left:right],
                                    "grammar", "Suggested grammar correction.",
                                    (replacement,), replacement, "medium", False))
    return tuple(issues)


def merge_proofreading_selections(editor, selections):
    """Replace our overlays without removing semantic colors or other overlays."""
    external = [item for item in editor.extraSelections()
                if not item.format.property(PROOFREADING_SELECTION_PROPERTY)]
    for selection in selections:
        selection.format.setProperty(PROOFREADING_SELECTION_PROPERTY, True)
    editor.setExtraSelections(external + list(selections))


def _offsets(text):
    result = [0]
    for char in text:
        result.append(result[-1] + (2 if ord(char) > 0xFFFF else 1))
    return result


def _icon(widget):
    pixmap = QtGui.QPixmap(24, 24)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    color = widget.palette().color(QtGui.QPalette.ButtonText)
    painter.setPen(QtGui.QPen(color, 1.8, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    font = QtGui.QFont(widget.font())
    font.setPixelSize(15)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QtCore.QRect(1, 0, 17, 18), QtCore.Qt.AlignCenter, "A")
    painter.drawLine(9, 19, 13, 22)
    painter.drawLine(13, 22, 23, 11)
    painter.end()
    return QtGui.QIcon(pixmap)


class ProofreadingBinding(QtCore.QObject):
    _ai_reply = QtCore.Signal(object)

    def __init__(self, editor, *, service=None, context="prose", protected_terms=None,
                 identity=None, button=None):
        super().__init__(editor)
        self.editor = editor
        self.service = service if service is not None else get_language_service(
            getattr(editor, "project_root", None))
        self.context = context
        self.protected_terms = protected_terms or (lambda: ())
        self.identity = identity or (lambda: None)
        self._dictionary_issues = ()
        self._ai_issues = ()
        self._ignored = set()
        self._issue_source = editor.toPlainText()
        self._last_text = self._issue_source
        self._auto_ranges = []
        self._issues_snapshot = None
        self._baseline = self._issue_source
        self._pending_auto = False
        self._authored_source = None
        self._complete_end = False
        self._ai_dirty = False
        self._user_event = False
        self._undo_event = False
        self._preedit = False
        self._applying = False
        self._suspended = 0
        self._generation = 0
        self._chunk_token = 0
        self._ai_queue = []
        self._ai_snapshot = None
        self._ai_error = ""
        self._grammar = None
        self._dictation = None
        self._next_shortcut = None
        self._previous_shortcut = None
        self._embedded = button is None
        self.button = button or QtWidgets.QToolButton(editor)
        if self._embedded:
            self.button.setFixedSize(28, 28)
            self.button.setAutoRaise(True)
            self.button.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
            self.button.setIconSize(QtCore.QSize(20, 20))
            self.button.setStyleSheet("QToolButton { padding: 0px; }")
            margins = editor.viewportMargins()
            editor.setViewportMargins(margins.left(), margins.top(),
                                      margins.right() + 36, margins.bottom())
        self.button.setObjectName(self.button.objectName() or "proofreadingButton")
        self.button.setAccessibleName("Writing Suggestions")
        self.button.setFocusPolicy(QtCore.Qt.NoFocus)
        self.button.clicked.connect(self.show_suggestions)
        self._paint_timer = QtCore.QTimer(self)
        self._paint_timer.setSingleShot(True)
        self._paint_timer.setInterval(0)
        self._paint_timer.timeout.connect(self._paint_issues)
        self._dictionary_timer = QtCore.QTimer(self)
        self._dictionary_timer.setSingleShot(True)
        self._dictionary_timer.setInterval(550)
        self._dictionary_timer.timeout.connect(self._check_dictionary)
        self._ai_timer = QtCore.QTimer(self)
        self._ai_timer.setSingleShot(True)
        self._ai_timer.setInterval(1500)
        self._ai_timer.timeout.connect(self._check_ai)
        self._ai_reply.connect(self._receive_ai, QtCore.Qt.QueuedConnection)
        editor.installEventFilter(self)
        editor.viewport().installEventFilter(self)
        editor.verticalScrollBar().installEventFilter(self)
        editor.document().contentsChanged.connect(self._contents_changed)
        editor.destroyed.connect(self.cancel)
        self._connect_dictation()
        self.refresh()

    def _terms(self):
        try:
            supplied = tuple(self.protected_terms())
        except Exception:
            LOGGER.exception("Could not read proofreading protected terms")
            supplied = ()
        return tuple(dict.fromkeys((*supplied, *getattr(self.service, "protected_words", ()))))

    def _snapshot(self):
        document = self.editor.document()
        return document, document.revision(), self.editor.toPlainText(), self.identity()

    def _valid(self, *, visible=True):
        return (isValid(self.editor) and self.editor.isEnabled()
                and not self.editor.isReadOnly()
                and (not visible or self.editor.isVisible()) and not self._suspended)

    def _connect_dictation(self):
        binding = getattr(self.editor, "_dictation_binding", None)
        if binding is not None and binding is not self._dictation:
            self._dictation = binding
            finished = getattr(binding, "finished", None)
            if finished is not None:
                finished.connect(self._dictation_finished)

    def _recording(self):
        self._connect_dictation()
        return bool(self._dictation and (self._dictation.active or self._dictation.finishing))

    def _dictation_finished(self):
        if self._valid() and not self._preedit:
            if self.editor.toPlainText() == self._authored_source:
                self._pending_auto = self._complete_end = True
            self._dictionary_timer.start()
            if self._ai_dirty:
                self._ai_timer.start()

    @property
    def issues(self):
        unique = {}
        for issue in (*self._dictionary_issues, *self._ai_issues):
            if not self.is_issue_ignored(issue):
                unique.setdefault((issue.start, issue.end, issue.replacement), issue)
        return tuple(sorted(unique.values(), key=lambda item: (item.start, item.end)))

    def set_issues(self, issues):
        if not isValid(self.editor):
            return
        self._dictionary_issues = tuple(issues)
        self._issue_source = self.editor.toPlainText()
        self._issues_snapshot = self._snapshot()
        self._paint_issues()

    def is_issue_ignored(self, issue):
        key = (str(getattr(issue, "text", "")).casefold(),
               str(getattr(issue, "category", "")))
        return key in self._ignored

    def _paint_issues(self):
        text = self.editor.toPlainText()
        offsets = _offsets(text)
        selections = []
        for issue in self.issues:
            start, end = int(issue.start), int(issue.end)
            if not 0 <= start <= end <= len(text):
                continue
            if start == end:
                start, end = (max(0, start - 1), start) if start else (0, min(1, len(text)))
            if start == end:
                continue
            selection = QtWidgets.QTextEdit.ExtraSelection()
            cursor = QtGui.QTextCursor(self.editor.document())
            cursor.setPosition(offsets[start])
            cursor.setPosition(offsets[end], QtGui.QTextCursor.KeepAnchor)
            selection.cursor = cursor
            selection.format.setUnderlineColor(QtGui.QColor(
                "#ff6b7d" if issue.category == "spelling" else "#f5b84b"))
            selection.format.setUnderlineStyle(QtGui.QTextCharFormat.SpellCheckUnderline)
            selections.append(selection)
        merge_proofreading_selections(self.editor, selections)
        self._refresh_button()

    def _refresh_button(self):
        if not isValid(self.button):
            return
        self.button.setIcon(_icon(self.button))
        count = len(self.issues)
        tooltip = "Writing Suggestions" + (f" ({count})" if count else "")
        if self._next_shortcut is not None:
            tooltip += f"\nNext suggestion ({self._next_shortcut.key().toString()})"
        if self._previous_shortcut is not None:
            tooltip += f"\nPrevious suggestion ({self._previous_shortcut.key().toString()})"
        if self._ai_error:
            tooltip += "\n" + self._ai_error
        self.button.setToolTip(tooltip)
        self.button.setEnabled(self.editor.isEnabled() and not self.editor.isReadOnly())
        self._position_button()

    def _position_button(self):
        if not self._embedded or not isValid(self.editor):
            return
        rect = self.editor.contentsRect()
        right = rect.right() - 3
        scrollbar = self.editor.verticalScrollBar()
        if scrollbar.isVisible():
            scrollbar_x = scrollbar.mapTo(self.editor, QtCore.QPoint()).x()
            if scrollbar_x > rect.center().x():
                right = min(right, scrollbar_x - 4)
        dictation = getattr(self.editor, "_dictation_binding", None)
        if dictation is not None and dictation.embedded:
            right -= 32
        self.button.move(right - self.button.width(), rect.top() + 3)
        self.button.raise_()

    def _begin_user_event(self, *, undo=False):
        self._user_event = not undo
        self._undo_event = undo
        QtCore.QTimer.singleShot(0, self._end_user_event)

    def _end_user_event(self):
        self._user_event = self._undo_event = False

    def _contents_changed(self):
        if not isValid(self.editor) or self._applying:
            return
        self._cancel_ai()
        self._dictionary_issues = self._ai_issues = ()
        self._issue_source = self.editor.toPlainText()
        previous, self._last_text = self._last_text, self._issue_source
        authored = self._user_event and not self._undo_event and not self._suspended
        if authored:
            self._track_authored_range(previous, self._issue_source)
        else:
            self._auto_ranges = []
        self._pending_auto = bool(authored)
        self._authored_source = self._issue_source if authored else None
        self._complete_end = False
        self._ai_dirty = bool(authored or self._undo_event)
        if not authored and not self._undo_event:
            self._baseline = self._issue_source
        self._paint_timer.start()
        if not self._suspended and not self._preedit:
            self._dictionary_timer.start()
            if self._ai_dirty:
                self._ai_timer.start()

    def _track_authored_range(self, before, after):
        first = 0
        limit = min(len(before), len(after))
        while first < limit and before[first] == after[first]:
            first += 1
        tail = 0
        while tail < limit - first and before[-tail - 1] == after[-tail - 1]:
            tail += 1
        old_end, new_end = len(before) - tail, len(after) - tail
        difference = new_end - old_end
        mapped = []
        for start, end in self._auto_ranges:
            if end <= first:
                mapped.append((start, end))
            elif start >= old_end:
                mapped.append((start + difference, end + difference))
            else:
                mapped.append((min(start, first), max(new_end, end + difference)))
        left, right = first, new_end
        while left and (after[left - 1].isalpha() or after[left - 1] in "'-\u2019"):
            left -= 1
        while right < len(after) and (after[right].isalpha() or after[right] in "'-\u2019"):
            right += 1
        mapped.append((left, right))
        self._auto_ranges = mapped

    @contextmanager
    def suspend(self):
        self._suspended += 1
        self.cancel()
        try:
            yield self
        finally:
            self._suspended -= 1
            if not self._suspended and isValid(self.editor):
                self._pending_auto = self._ai_dirty = False
                self._baseline = self.editor.toPlainText()
                self.refresh()

    def refresh(self):
        if isValid(self.editor):
            self._refresh_button()
            if not self._suspended:
                self._dictionary_timer.start()

    def _check_dictionary(self, *, auto_correct=True):
        if not self._valid() or self._preedit:
            return
        analyze = getattr(self.service, "get_issues", None)
        if not callable(analyze):
            return
        try:
            source = self.editor.toPlainText()
            issues = analyze(source, context=self.context, protected_terms=self._terms())
            self.set_issues(issues)
            if auto_correct and self._pending_auto and not self._recording():
                cursor = self.editor.textCursor()
                qt_position = cursor.position()
                offsets = _offsets(source)
                position = next((i for i, value in enumerate(offsets) if value >= qt_position), len(source))
                completed = [issue for issue in issues
                             if is_automatic_spelling_fix(issue) and not self.is_issue_ignored(issue)
                             and any(issue.start < end and issue.end > start for start, end in self._auto_ranges)
                             and (issue.end == len(source) and self._complete_end
                                  or issue.end < len(source) and not source[issue.end].isalpha())
                             and (self._complete_end or not (issue.start <= position <= issue.end))]
                self._pending_auto = False
                self._auto_ranges = []
                if completed:
                    self.apply_replacements(completed)
        except Exception:
            LOGGER.exception("Could not check writing suggestions")

    def _changed_chunks(self, source, force=False):
        return list(proofreading_chunks(source, "" if force else self._baseline))

    def _check_ai(self, force=False):
        if not self._valid() or self._preedit or self._recording() or not (force or self._ai_dirty):
            return
        self._cancel_ai()
        source = self.editor.toPlainText()
        self._ai_queue = self._changed_chunks(source, force)
        self._ai_snapshot = self._snapshot()
        self._ai_dirty = False
        if not self._ai_queue:
            return
        self._baseline = source
        try:
            from language_ai import get_grammar_service
            self._grammar = get_grammar_service()
            self._next_ai()
        except Exception:
            LOGGER.exception("Could not start local AI proofreading")
            self._ai_error = "AI proofreading is unavailable. Open AI proofreading settings."
            self._refresh_button()

    def _next_ai(self):
        if not self._ai_queue or not self._valid() or self._snapshot() != self._ai_snapshot:
            return
        start, original = self._ai_queue.pop(0)
        generation = self._generation
        self._chunk_token += 1
        token = self._chunk_token
        reference = weakref.ref(self)
        def completed(request_id, corrected, error):
            binding = reference()
            if binding is not None and isValid(binding):
                binding._ai_reply.emit((generation, token, start, original, corrected, error))
        try:
            self._grammar.submit(self, original, context=self.context,
                                 protected_terms=self._terms(), callback=completed)
        except Exception:
            LOGGER.exception("Could not request local AI proofreading")
            self._ai_queue = []
            self._ai_error = "AI proofreading is unavailable. Open AI proofreading settings."
            self._refresh_button()

    def _receive_ai(self, result):
        generation, token, start, original, corrected, error = result
        if (generation != self._generation or token != self._chunk_token
                or not self._valid() or self._preedit or self._recording()
                or self._snapshot() != self._ai_snapshot):
            return
        if error:
            self._ai_error = str(error)
            self._ai_queue = []
        elif isinstance(corrected, str):
            self._ai_error = ""
            self._ai_issues += self._diff_issues(start, original, corrected)
            self._issues_snapshot = self._snapshot()
        self._paint_issues()
        self._next_ai()

    def _diff_issues(self, start, original, corrected):
        protected = getattr(self.service, "_protected_ranges", lambda text, terms: ())(
            original, self._terms())
        return grammar_issues(start, original, corrected, protected)

    def _cancel_ai(self):
        self._generation += 1
        self._ai_queue = []
        self._ai_snapshot = None
        if self._grammar is not None:
            self._grammar.cancel(self)

    def cancel(self, *_):
        self._paint_timer.stop()
        self._dictionary_timer.stop()
        self._ai_timer.stop()
        self._cancel_ai()
        self._pending_auto = self._ai_dirty = False
        self._auto_ranges = []
        self._preedit = False

    def replace_issue(self, issue, replacement):
        if (not self._valid(visible=False) or self._snapshot() != self._issues_snapshot
                or not any(current is issue for current in self.issues)):
            return False
        from dataclasses import replace
        try:
            updated = replace(issue, replacement=str(replacement))
        except TypeError:
            updated = LanguageIssue(issue.start, issue.end, issue.text, issue.category,
                                    getattr(issue, "message", ""), (str(replacement),), str(replacement))
        return bool(self.apply_replacements((updated,)))

    def apply_replacements(self, issues, *, edit_cursor=None, emit_refresh=True):
        if not self._valid(visible=False) or self._preedit or self._recording():
            return 0
        text = self.editor.toPlainText()
        replacements = sorted((issue for issue in issues if getattr(issue, "replacement", None) is not None),
                              key=lambda issue: (issue.start, issue.end), reverse=True)
        valid = []
        previous = len(text) + 1
        for issue in replacements:
            if (0 <= issue.start <= issue.end <= len(text) and issue.end <= previous
                    and text[issue.start:issue.end] == issue.text):
                valid.append(issue)
                previous = issue.start
        if not valid:
            return 0
        cursor = edit_cursor or QtGui.QTextCursor(self.editor.document())
        owns_block = edit_cursor is None
        offsets = _offsets(text)
        saved_cursor = QtGui.QTextCursor(self.editor.textCursor())
        self._applying = True
        describe = getattr(self.editor, "describe_edit", None)
        with (describe("Proofreading correction") if describe else nullcontext()):
            if owns_block:
                cursor.beginEditBlock()
            try:
                for issue in valid:
                    cursor.setPosition(offsets[issue.start])
                    cursor.setPosition(offsets[issue.end], QtGui.QTextCursor.KeepAnchor)
                    char_format = QtGui.QTextCharFormat(cursor.charFormat())
                    cursor.insertText(str(issue.replacement), char_format)
                self.editor.setTextCursor(saved_cursor)
            finally:
                if owns_block:
                    cursor.endEditBlock()
                self._applying = False
        self._pending_auto = False
        self._auto_ranges = []
        self._last_text = self.editor.toPlainText()
        self._ai_dirty = True
        self._cancel_ai()
        self._dictionary_issues = self._ai_issues = ()
        self._paint_issues()
        self._dictionary_timer.start()
        self._ai_timer.start()
        signal = getattr(self.editor, "language_refresh_requested", None)
        if emit_refresh and signal is not None:
            signal.emit()
        return len(valid)

    def ignore_issue(self, issue):
        self._ignored.add((str(issue.text).casefold(), str(issue.category)))
        if issue.category == "spelling":
            ignore_word = getattr(self.service, "ignore_word", None)
            if callable(ignore_word):
                try:
                    ignore_word(issue.text)
                except Exception:
                    LOGGER.exception("Could not ignore a word in the shared dictionary")
        self._cancel_ai()
        self._ai_issues = ()
        self._paint_issues()
        self.refresh()

    def add_to_dictionary(self, issue):
        try:
            added = bool(self.service.add_to_dictionary(issue.text))
        except Exception:
            LOGGER.exception("Could not add a word to the dictionary")
            return False
        signal = getattr(self.editor, "language_refresh_requested", None)
        if signal is not None:
            signal.emit()
        self._cancel_ai()
        self._ai_issues = ()
        self._paint_issues()
        self.refresh()
        return added

    ignore = ignore_issue
    add_dictionary = add_to_dictionary

    def _populate_menu(self, menu, issue=None):
        issues = (issue,) if issue is not None else self.issues
        snapshot = self._snapshot()
        if not issues:
            menu.addAction("No writing suggestions").setEnabled(False)
        for current in issues[:40]:
            target = menu if issue is not None else menu.addMenu(current.message)
            suggestions = tuple(getattr(current, "suggestions", ()))
            replacement = getattr(current, "replacement", None)
            if replacement is not None and replacement not in suggestions:
                suggestions = (replacement, *suggestions)
            for suggestion in suggestions[:5]:
                label = "Remove" if not suggestion else "Replace with " + suggestion
                action = target.addAction(label)
                action.triggered.connect(lambda _checked=False, item=current, value=suggestion, expected=snapshot:
                                         self.replace_issue(item, value) if self._snapshot() == expected else False)
            target.addAction("Ignore", lambda _checked=False, item=current: self.ignore_issue(item))
            if current.category == "spelling":
                target.addAction("Add to Dictionary", lambda _checked=False, item=current: self.add_to_dictionary(item))
        menu.addSeparator()
        menu.addAction("AI proofreading settings…", self._open_setup)

    def _open_setup(self):
        self.cancel()
        try:
            from language_ai import open_setup
            open_setup(self.editor.window())
        except Exception:
            LOGGER.exception("Could not open AI proofreading settings")

    def show_suggestions(self):
        if not self._valid() or self._recording():
            return
        self._complete_end = True
        self._check_dictionary()
        try:
            from language_ai import get_grammar_service
            if not get_grammar_service().ready():
                self._open_setup()
                return
        except Exception:
            LOGGER.exception("Could not check local AI proofreading readiness")
        self._check_ai(force=True)
        menu = QtWidgets.QMenu(self.button)
        self._populate_menu(menu)
        menu.exec(self.button.mapToGlobal(QtCore.QPoint(0, self.button.height())))
        menu.deleteLater()

    def enable_next_suggestion_shortcut(self, key, *, previous_key=None):
        if self._next_shortcut is None:
            self._next_shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self.editor)
            self._next_shortcut.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
            self._next_shortcut.activated.connect(self.select_next_suggestion)
        if previous_key and self._previous_shortcut is None:
            self._previous_shortcut = QtGui.QShortcut(QtGui.QKeySequence(previous_key), self.editor)
            self._previous_shortcut.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
            self._previous_shortcut.activated.connect(lambda: self.select_next_suggestion(backward=True))
        self._refresh_button()

    def select_next_suggestion(self, *, backward=False):
        if not self._valid() or self._recording() or self._preedit:
            return False
        if self._issues_snapshot != self._snapshot():
            self._check_dictionary(auto_correct=False)
        offsets = _offsets(self.editor.toPlainText())
        ranges = [(offsets[issue.start], offsets[issue.end]) for issue in self.issues
                  if 0 <= issue.start <= issue.end < len(offsets)]
        if not ranges:
            return False
        cursor = self.editor.textCursor()
        selection = (cursor.selectionStart(), cursor.selectionEnd())
        if backward:
            start, end = next((span for span in reversed(ranges)
                               if span[1] <= selection[0] and span != selection), ranges[-1])
        else:
            start, end = next((span for span in ranges
                               if span[0] >= selection[1] and span != selection), ranges[0])
        cursor.setPosition(start)
        cursor.setPosition(end, QtGui.QTextCursor.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()
        return True

    def _context_menu(self, event):
        menu = self.editor.createStandardContextMenu()
        for action in menu.actions():
            label = (action.objectName().removeprefix("edit-")
                     or action.text().replace("&", "").split("\t")[0].casefold())
            callbacks = {"undo": self.editor.undo, "redo": self.editor.redo,
                         "paste": self.editor.paste, "cut": self.editor.cut,
                         "delete": lambda: self.editor.textCursor().removeSelectedText()}
            if label in callbacks:
                action.triggered.disconnect()
                def invoke(_checked=False, name=label, callback=callbacks[label]):
                    self._begin_user_event(undo=name in {"undo", "redo"})
                    describe = getattr(self.editor, "describe_edit", None)
                    with (describe(name.capitalize()) if describe else nullcontext()):
                        callback()
                action.triggered.connect(invoke)
        position = self.editor.cursorForPosition(event.pos()).position()
        offsets = _offsets(self.editor.toPlainText())
        python_position = max(0, next((i for i, value in enumerate(offsets) if value >= position), len(offsets) - 1))
        issue = next((item for item in self.issues if item.start <= python_position <= item.end), None)
        suggestions = QtWidgets.QMenu("Writing Suggestions", menu)
        self._populate_menu(suggestions, issue)
        first = menu.actions()[0] if menu.actions() else None
        menu.insertMenu(first, suggestions)
        menu.insertSeparator(first)
        menu.exec(event.globalPos())
        menu.deleteLater()

    def eventFilter(self, watched, event):
        if not isValid(self.editor):
            return False
        kind = event.type()
        if watched in {self.editor, self.editor.viewport()}:
            if kind == QtCore.QEvent.KeyPress:
                undo = event.matches(QtGui.QKeySequence.Undo) or event.matches(QtGui.QKeySequence.Redo)
                self._begin_user_event(undo=undo)
            elif kind == QtCore.QEvent.InputMethod:
                self._preedit = bool(event.preeditString())
                if event.commitString():
                    self._begin_user_event()
            elif kind == QtCore.QEvent.Drop:
                self._begin_user_event()
            elif kind == QtCore.QEvent.FocusOut and self.editor.toPlainText() == self._authored_source:
                self._pending_auto = self._complete_end = True
                self._dictionary_timer.start()
            elif kind == QtCore.QEvent.ContextMenu and not self.editor.isReadOnly():
                self._context_menu(event)
                return True
        if watched is self.editor:
            if kind in {QtCore.QEvent.Hide, QtCore.QEvent.Close}:
                self.cancel()
            elif kind in {QtCore.QEvent.Show, QtCore.QEvent.PaletteChange,
                          QtCore.QEvent.EnabledChange, QtCore.QEvent.ReadOnlyChange}:
                self.refresh()
            elif kind == QtCore.QEvent.Resize:
                self._position_button()
        elif watched is self.editor.verticalScrollBar() and kind in {
                QtCore.QEvent.Show, QtCore.QEvent.Hide, QtCore.QEvent.Move, QtCore.QEvent.Resize}:
            self._position_button()
        return False


def attach_proofreading(editor, *, service=None, context="prose", protected_terms=None,
                        identity=None, button=None):
    if not isinstance(editor, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        raise TypeError("proofreading requires a QTextEdit or QPlainTextEdit")
    binding = getattr(editor, "_proofreading_binding", None)
    if binding is None:
        binding = ProofreadingBinding(editor, service=service, context=context,
                                     protected_terms=protected_terms, identity=identity, button=button)
        editor._proofreading_binding = binding
    return binding
