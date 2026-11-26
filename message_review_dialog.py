"""Search and proofreading reviews for the Letter pages in Messages."""
from __future__ import annotations

from bisect import bisect_left
from collections import deque
from dataclasses import replace

from PySide6 import QtCore, QtWidgets

from message_page_tools import TextEdit, find_text
from proofreading import grammar_issues, proofreading_chunks
from ui_dialogs import LetterSmithDialog


_DATA = QtCore.Qt.UserRole


def _result_tree(parent, columns):
    tree = QtWidgets.QTreeWidget(parent)
    tree.setHeaderLabels(columns)
    tree.setAlternatingRowColors(True)
    tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
    return tree


def _add_page(tree, page):
    item = QtWidgets.QTreeWidgetItem(tree, [page.label])
    item.setExpanded(True)
    return item


class LetterSearchDialog(LetterSmithDialog):
    def __init__(self, workspace, selected_text):
        super().__init__(workspace.tool_parent(), title="Find and Replace in Letter", width=640)
        self.workspace = workspace
        self.matches = []
        editor = workspace.editor.editor
        cursor = editor.textCursor()
        self._selection_source = editor.toPlainText()
        self._selection_page = workspace.page_id
        self._selection_session = workspace.session.session_id
        offsets = editor._python_to_qt_offsets(self._selection_source)
        self._selection_start = bisect_left(offsets, cursor.selectionStart())
        self._selection_end = bisect_left(offsets, cursor.selectionEnd())
        form = QtWidgets.QFormLayout()
        self.query = QtWidgets.QLineEdit(selected_text)
        self.replacement = QtWidgets.QLineEdit()
        self.match_case = QtWidgets.QCheckBox("Match case")
        self.selection_only = QtWidgets.QCheckBox("Selection Only")
        self.selection_only.setEnabled(cursor.hasSelection())
        form.addRow("Find", self.query)
        form.addRow("Replace with", self.replacement)
        form.addRow(self.match_case)
        form.addRow(self.selection_only)
        self.content_layout.addLayout(form)
        self.results = _result_tree(self, ["Matches by page"])
        self.content_layout.addWidget(self.results, 1)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.content_layout.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        find = buttons.addButton("Find All", QtWidgets.QDialogButtonBox.ActionRole)
        self.replace_match = buttons.addButton("Replace Match", QtWidgets.QDialogButtonBox.ActionRole)
        self.replace_all = buttons.addButton("Replace All", QtWidgets.QDialogButtonBox.ActionRole)
        for button in (find, self.replace_match, self.replace_all):
            button.setAutoDefault(False)
        buttons.rejected.connect(self.reject)
        find.clicked.connect(self.search)
        self.query.returnPressed.connect(self.search)
        self.query.textChanged.connect(self.clear_results)
        self.match_case.toggled.connect(self.clear_results)
        self.selection_only.toggled.connect(self.clear_results)
        self.results.itemSelectionChanged.connect(self.update_buttons)
        self.results.itemDoubleClicked.connect(self.go_to_match)
        self.replace_match.clicked.connect(lambda: self.apply_matches(selected_only=True))
        self.replace_all.clicked.connect(self.apply_matches)
        self.content_layout.addWidget(buttons)
        self.update_buttons()
        self.query.setFocus()
        self.query.selectAll()
        self.resize(680, 470)
        if selected_text:
            self.search()

    def clear_results(self):
        self.matches.clear()
        self.results.clear()
        self.status.clear()
        self.update_buttons()

    def search(self):
        self.clear_results()
        query = self.query.text()
        if not query:
            return
        pages = self.workspace.review_pages()
        if not pages:
            self.status.setText("Save the current draft before searching.")
            return
        if self.selection_only.isChecked():
            pages = tuple(page for page in pages if page.target.page_id == self._selection_page)
            if (not pages or pages[0].text != self._selection_source
                    or self.workspace.session.session_id != self._selection_session):
                self.status.setText("The selection changed. Reopen Find and Replace to select its current text.")
                return
        self.pages = pages
        for page in pages:
            start, end = ((self._selection_start, self._selection_end) if self.selection_only.isChecked()
                          else (0, len(page.text)))
            found = find_text(page.text[start:end], query, match_case=self.match_case.isChecked())
            edits = tuple(replace(match, start=match.start + start, end=match.end + start) for match in found)
            if not edits:
                continue
            self.matches.append((page, edits))
            group = _add_page(self.results, page)
            for match in edits:
                before = " ".join(page.text[max(0, match.start - 32):match.start].splitlines())
                after = " ".join(page.text[match.end:match.end + 32].splitlines())
                row = QtWidgets.QTreeWidgetItem(group, [f"{before}‹{match.text}›{after}"])
                row.setData(0, _DATA, (page, match))
        count = sum(len(edits) for _, edits in self.matches)
        if not count:
            self.status.setText("No matches found.")
        elif self.selection_only.isChecked():
            self.status.setText(f"{count} matches in the selection.")
        else:
            self.status.setText(f"{count} matches across {len(self.matches)} pages.")
        self.update_buttons()

    def update_buttons(self):
        item = self.results.currentItem()
        self.replace_match.setEnabled(bool(item and item.data(0, _DATA)))
        self.replace_all.setEnabled(bool(self.matches))

    def go_to_match(self, item, _column):
        data = item.data(0, _DATA)
        if not data:
            return
        if self.workspace.open_match(*data):
            self.accept()
        else:
            self.status.setText("This match changed. Find All again to refresh the results.")

    def apply_matches(self, *, selected_only=False):
        matches = self.matches
        if selected_only:
            page, match = self.results.currentItem().data(0, _DATA)
            matches = [(page, (match,))]
        value = self.replacement.text()
        edits = {page.target.page_id: tuple(replace(match, replacement=value) for match in entries)
                 for page, entries in matches}
        if self.selection_only.isChecked():
            page = self.pages[0]
            changes = edits.get(page.target.page_id, ())
            applied = self.workspace.apply_selection_edits(page, changes)
            if applied:
                source = self.workspace.editor.editor.toPlainText()
                self._selection_end += len(source) - len(self._selection_source)
                self._selection_source = source
        else:
            applied = self.workspace.apply_text_edits(self.pages, edits, "Matches replaced.")
        if applied:
            self.search()
        else:
            self.clear_results()
            self.status.setText(self.workspace.notice.text())


class LetterProofreadingDialog(LetterSmithDialog):
    def __init__(self, workspace, pages, service, terms, ignored):
        super().__init__(workspace.tool_parent(), title="Proofread Letter", width=650)
        self.workspace, self.pages, self.service, self.terms = workspace, pages, service, terms
        self.ignored = ignored
        self.results = _result_tree(self, ["Suggestion by page", "Correction"])
        self.content_layout.addWidget(self.results, 1)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.content_layout.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        self.apply_button = buttons.addButton("Apply Suggestion", QtWidgets.QDialogButtonBox.ActionRole)
        self.apply_button.setAutoDefault(False)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply_suggestion)
        buttons.rejected.connect(self.reject)
        self.content_layout.addWidget(buttons)
        self.results.itemSelectionChanged.connect(self.selection_changed)
        self.resize(690, 480)
        self.seen = {page.target.page_id: set() for page in pages}
        self.groups = {page.target.page_id: _add_page(self.results, page) for page in pages}
        for page in pages:
            self.add_issues(page, service.get_issues(page.text, context="prose", protected_terms=terms))
        from language_ai import get_grammar_service
        self.grammar = get_grammar_service()
        self.finished.connect(lambda _: self.grammar.cancel(self))
        self.queue = deque((page, start, text) for page in pages
                           for start, text in proofreading_chunks(page.text)) if self.grammar.ready() else deque()
        self.next_grammar_check()

    def add_issues(self, page, issues):
        page_id = page.target.page_id
        for issue in issues:
            suggestion = issue.replacement
            if suggestion is None and issue.suggestions:
                suggestion = issue.suggestions[0]
            key = (issue.start, issue.end, suggestion)
            if key in self.seen[page_id] or (str(issue.text).casefold(), issue.category) in self.ignored:
                continue
            self.seen[page_id].add(key)
            excerpt = " ".join(issue.text.split()) or "Insertion"
            label = f"{excerpt} — {issue.message}"
            correction = "Remove" if suggestion == "" else suggestion or ""
            item = QtWidgets.QTreeWidgetItem(self.groups[page_id], [label, correction])
            item.setToolTip(0, label)
            if suggestion is not None:
                item.setData(0, _DATA, TextEdit(issue.start, issue.end, issue.text, suggestion))

    def next_grammar_check(self):
        if not self.queue:
            self.finish_review()
            return
        self.status.setText("Checking local grammar…")
        page, start, original = self.queue.popleft()

        def completed(_identity, corrected, error):
            if error:
                self.queue.clear()
                self.finish_review(str(error))
                return
            protected = self.service._protected_ranges(original, self.terms)
            self.add_issues(page, grammar_issues(start, original, corrected, protected))
            self.next_grammar_check()

        self.grammar.submit(self, original, context="prose", protected_terms=self.terms, callback=completed)

    def finish_review(self, error=""):
        count = sum(len(items) for items in self.seen.values())
        status = f"{count} writing suggestions." if count else "No writing suggestions."
        self.status.setText(f"{status} Local grammar check failed. {error}" if error else status)
        for group in self.groups.values():
            if not group.childCount():
                group.setText(1, "No suggestions")

    def selection_changed(self):
        item = self.results.currentItem()
        self.apply_button.setEnabled(bool(item and item.data(0, _DATA)))

    def apply_suggestion(self):
        item = self.results.currentItem()
        edit = item.data(0, _DATA)
        page = next(page for page in self.pages if self.groups[page.target.page_id] is item.parent())
        if self.workspace.apply_text_edits(self.pages, {page.target.page_id: (edit,)}, "Suggestion applied."):
            self.accept()
        else:
            self.status.setText(self.workspace.notice.text())
