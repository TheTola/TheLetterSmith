from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

from Editor import Editor
from message_html import extract_lettersmith_style_state
from named_text_styles import STYLE_KEYS, STYLE_LABELS, default_style_set
from ui_fonts import load_application_fonts


class _NoIssues:
    def get_issues(self, *_args, **_kwargs):
        return ()


class NamedTextStyleEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.holder = tempfile.TemporaryDirectory()
        self.root = Path(self.holder.name)
        self.editors: list[Editor] = []
        self.hosts: list[QtWidgets.QWidget] = []

    def tearDown(self) -> None:
        for editor in self.editors:
            editor._autosave_timer.stop()
            editor.hide()
            editor.deleteLater()
        for host in self.hosts:
            host.deleteLater()
        self.app.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(
            None, QtCore.QEvent.DeferredDelete
        )
        self.holder.cleanup()

    def _editor(self, html: str = "<p>Alpha</p>") -> Editor:
        host = QtWidgets.QWidget()
        host.project_root = self.root
        editor = Editor(html, parent=host, language_service=_NoIssues())
        self.hosts.append(host)
        self.editors.append(editor)
        editor._autosave_timer.stop()
        return editor

    @staticmethod
    def _select(editor: Editor, start: int, end: int | None = None) -> None:
        cursor = editor.editor.textCursor()
        cursor.setPosition(start)
        if end is not None:
            cursor.setPosition(end, QtGui.QTextCursor.KeepAnchor)
        editor.editor.setTextCursor(cursor)

    @staticmethod
    def _format_at(editor: Editor, start: int) -> QtGui.QTextCharFormat:
        cursor = QtGui.QTextCursor(editor.editor.document())
        cursor.setPosition(start)
        cursor.movePosition(
            QtGui.QTextCursor.NextCharacter, QtGui.QTextCursor.KeepAnchor
        )
        return cursor.charFormat()

    @classmethod
    def _family_at(cls, editor: Editor, start: int) -> str:
        return str(cls._format_at(editor, start).fontFamilies()[0])

    def test_menu_has_exact_six_styles_horizontal_slot_row_and_options(self) -> None:
        editor = self._editor()
        actions = editor.named_style_menu.actions()
        self.assertEqual([action.text() for action in actions[:6]], [
            STYLE_LABELS[key] for key in STYLE_KEYS
        ])
        for key, action in zip(STYLE_KEYS, actions[:6]):
            self.assertIsNotNone(action.menu())
            self.assertEqual(
                [item.text() for item in action.menu().actions()],
                [f"Apply {STYLE_LABELS[key]}",
                 f"Update {STYLE_LABELS[key]} to match"],
            )
        self.assertIsInstance(actions[6], QtWidgets.QWidgetAction)
        row = actions[6].defaultWidget()
        self.assertIsInstance(row.layout(), QtWidgets.QHBoxLayout)
        self.assertEqual(
            [row.layout().itemAt(i).widget().text() for i in range(3)],
            ["Style 1", "Style 2", "Style 3"],
        )
        self.assertTrue(actions[7].isSeparator())
        self.assertEqual(actions[8].text(), "Options")
        self.assertEqual(
            [item.text() for item in actions[8].menu().actions()],
            [f"Save current style set to Style {i}" for i in (1, 2, 3)],
        )
        self.assertIsNot(editor.btn_named_style, editor.font_combo)
        editor._refresh_named_style_menu()
        self.assertTrue(all(
            not editor._style_slot_buttons[i].isEnabled() for i in (1, 2, 3)
        ))

    def test_style_menu_and_saved_slot_follow_their_own_definitions(self) -> None:
        original_sheet = self.app.styleSheet()
        self.addCleanup(self.app.setStyleSheet, original_sheet)
        self.app.setStyleSheet("QWidget { font: 10pt 'Arial'; }")
        load_application_fonts(Path(__file__).resolve().parents[1])
        editor = self._editor()
        current = editor.named_styles.active_set
        current = current.update_style(
            "normal_text", replace(
                current.get("normal_text"), font_family="Source Sans 3",
            )
        )
        current = current.update_style(
            "subtitle", replace(
                current.get("subtitle"), font_family="Source Serif 4",
                font_size=23, font_color="#d92244",
            )
        )
        current = current.update_style(
            "heading_1", replace(
                current.get("heading_1"), font_family="Exo 2",
                font_color="#28cc88",
            )
        )
        editor.named_styles.load_set(current)
        editor._refresh_named_style_menu()
        normal_action, subtitle_action = editor.named_style_menu.actions()[:2]
        normal_label = normal_action._named_text_preview_row.label
        subtitle_label = subtitle_action._named_text_preview_row.label
        self.assertEqual(normal_label.font().family(), current.get("normal_text").font_family)
        self.assertEqual(subtitle_label.font().family(), "Source Serif 4")
        self.assertEqual(subtitle_label.font().pointSizeF(), 23)
        self.assertIn(
            QtGui.QColor(current.get("subtitle").font_color).name(QtGui.QColor.HexArgb),
            subtitle_label.styleSheet(),
        )
        self.assertNotEqual(normal_label.font().family(), subtitle_label.font().family())

        self._select(editor, 1)
        editor._apply_named_style("subtitle")
        self.assertEqual(editor.btn_named_style.text(), "Subtitle")
        self.assertEqual(editor.btn_named_style.font().family(), "Source Serif 4")
        self.assertIn(current.get("subtitle").font_color, editor.btn_named_style.styleSheet())

        editor._save_style_slot(1)
        saved_button = editor._style_slot_buttons[1]
        self.assertTrue(saved_button.isEnabled())
        self.assertEqual(saved_button.font().family(), "Exo 2")
        self.assertIn(
            QtGui.QColor(current.get("heading_1").font_color).name(QtGui.QColor.HexArgb),
            saved_button.styleSheet(),
        )

    def test_direct_font_does_not_update_style_apply_restores_it(self) -> None:
        editor = self._editor()
        self._select(editor, 1)
        editor._apply_named_style("normal_text")
        original = editor.named_styles.active_set
        self._select(editor, 0, 5)
        editor.set_font_family(QtGui.QFont("Arial"))
        self.assertEqual(editor.named_styles.active_set, original)
        self.assertIsNone(editor._style_slots.load(1))
        self.assertEqual(self._family_at(editor, 0), "Arial")
        editor._apply_named_style("normal_text")
        self.assertEqual(
            self._family_at(editor, 0),
            original.get("normal_text").font_family,
        )

    def test_explicit_normal_update_propagates_only_font(self) -> None:
        editor = self._editor()
        self._select(editor, 1)
        editor._apply_named_style("normal_text")
        before = editor.named_styles.active_set
        editor.set_font_family(QtGui.QFont("Arial"))
        editor._update_named_style("normal_text")
        after = editor.named_styles.active_set
        self.assertEqual(after.get("normal_text").font_family, "Arial")
        for key in STYLE_KEYS[1:]:
            self.assertEqual(after.get(key).font_family, "Arial")
            self.assertEqual(
                replace(after.get(key), font_family=before.get(key).font_family),
                before.get(key),
            )
        self.assertIsNone(editor._style_slots.load(1))

    def test_mixed_selection_disables_update_but_uniform_selection_allows_it(self) -> None:
        editor = self._editor("<p>Alpha Beta</p>")
        self._select(editor, 1)
        editor._apply_named_style("normal_text")
        self._select(editor, 0, 5)
        editor.set_font_family(QtGui.QFont("Arial"))
        self._select(editor, 0, len(editor.editor.toPlainText()))
        editor._refresh_named_style_menu()
        self.assertTrue(all(
            not action.isEnabled() for action in editor._style_update_actions.values()
        ))
        self.assertIn(
            "uniformly formatted",
            editor._style_update_actions["normal_text"].toolTip(),
        )
        self._select(editor, 0, 5)
        editor._refresh_named_style_menu()
        self.assertTrue(all(
            action.isEnabled() for action in editor._style_update_actions.values()
        ))

    def test_save_is_complete_and_loading_is_independent(self) -> None:
        editor = self._editor()
        self._select(editor, 1)
        editor._apply_named_style("normal_text")
        editor.set_font_family(QtGui.QFont("Arial"))
        before = editor.named_styles.active_set
        editor._save_style_slot(1)
        self.assertEqual(editor._style_slots.load(1), before)
        self.assertEqual(
            editor._style_slots.load(1).get("normal_text").font_family,
            default_style_set().get("normal_text").font_family,
        )
        changed = before.update_style(
            "title", replace(before.get("title"), font_family="Georgia")
        )
        editor.named_styles.load_set(changed)
        self.assertEqual(editor._style_slots.load(1), before)
        editor._load_style_slot(1)
        self.assertEqual(editor.named_styles.active_set, before)

    def test_selection_ending_at_next_paragraph_start_does_not_style_it(self) -> None:
        editor = self._editor("<p>First</p><p>Second</p>")
        boundary = editor.editor.document().firstBlock().next().position()
        self._select(editor, 0, boundary)
        editor._apply_named_style("heading_1")
        self._select(editor, 1)
        self.assertEqual(editor.named_styles.selected_style(), "heading_1")
        self._select(editor, boundary + 1)
        self.assertNotEqual(editor.named_styles.selected_style(), "heading_1")

    def test_style_load_preserves_direct_italic_override(self) -> None:
        editor = self._editor("<p>One</p><p>Two</p>")
        self._select(editor, 0, len(editor.editor.toPlainText()))
        editor._apply_named_style("heading_1")
        self._select(editor, 0, 3)
        fmt = QtGui.QTextCharFormat()
        fmt.setFontItalic(True)
        editor._apply_char_format(fmt)
        changed = editor.named_styles.active_set.update_style(
            "heading_1",
            replace(
                editor.named_styles.active_set.get("heading_1"),
                font_family="Georgia",
            ),
        )
        editor.named_styles.load_set(changed)
        self.assertEqual(self._family_at(editor, 0), "Georgia")
        self.assertTrue(self._format_at(editor, 0).fontItalic())
        second_start = editor.editor.document().firstBlock().next().position()
        self.assertEqual(self._family_at(editor, second_start), "Georgia")
        self.assertFalse(self._format_at(editor, second_start).fontItalic())

    def test_apply_regular_normal_clears_bold_underline_but_keeps_link(self) -> None:
        editor = self._editor(
            '<p><a href="https://example.com/letter">Link</a> text</p>'
        )
        self._select(editor, 0, 4)
        direct = QtGui.QTextCharFormat()
        direct.setFontWeight(700)
        direct.setFontUnderline(True)
        editor._apply_char_format(direct)
        self.assertGreaterEqual(self._format_at(editor, 0).fontWeight(), 700)
        editor._apply_named_style("normal_text")
        link_format = self._format_at(editor, 0)
        self.assertLess(link_format.fontWeight(), 700)
        self.assertFalse(link_format.fontUnderline())
        self.assertEqual(link_format.anchorHref(), "https://example.com/letter")
        self.assertIn('href="https://example.com/letter"', editor.get_edited_html())

    def test_undo_restores_named_style_assignment(self) -> None:
        editor = self._editor()
        self._select(editor, 1)
        before = editor.named_styles.selected_style()
        editor._apply_named_style("title")
        self.assertEqual(editor.named_styles.selected_style(), "title")
        editor.editor.undo()
        self.assertEqual(editor.named_styles.selected_style(), before)

    def test_salutation_uses_custom_title_and_normal_body_after_reopen(self) -> None:
        editor = self._editor("<p>Body</p>")
        editor.recipient_name = "Amani"
        title = replace(
            editor.named_styles.active_set.get("title"),
            font_family="Georgia",
            font_size=31,
            font_color="#c22544",
            italic=True,
        )
        editor.named_styles.load_set(
            editor.named_styles.active_set.update_style("title", title)
        )
        editor.insert_salutation()
        self.assertEqual(editor.editor.toPlainText(), "Dear Amani,\nBody")
        self.assertEqual(self._family_at(editor, 0), "Georgia")
        self.assertEqual(self._format_at(editor, 0).fontPointSize(), 31)
        self.assertEqual(
            self._format_at(editor, 0).foreground().color().name(), "#c22544"
        )
        self.assertTrue(self._format_at(editor, 0).fontItalic())
        self._select(editor, 1)
        self.assertEqual(editor.named_styles.selected_style(), "title")
        body_start = editor.editor.document().firstBlock().next().position()
        self._select(editor, body_start)
        self.assertEqual(editor.named_styles.selected_style(), "normal_text")

        reopened = self._editor(editor.get_edited_html())
        self.assertEqual(reopened.editor.toPlainText(), "Dear Amani,\nBody")
        self.assertEqual(self._family_at(reopened, 0), "Georgia")
        self._select(reopened, 1)
        self.assertEqual(reopened.named_styles.selected_style(), "title")
        self._select(reopened, reopened.editor.document().firstBlock().next().position())
        self.assertEqual(reopened.named_styles.selected_style(), "normal_text")

        editor.editor.undo()
        self.assertEqual(editor.editor.toPlainText(), "Body")

    def test_html_reopen_restores_letter_state_without_saved_slots(self) -> None:
        editor = self._editor("<p>Alpha</p>")
        self._select(editor, 1)
        editor._apply_named_style("heading_1")
        editor.set_font_family(QtGui.QFont("Georgia"))
        editor._update_named_style("heading_1")
        self._select(editor, 0, 5)
        italic = QtGui.QTextCharFormat()
        italic.setFontItalic(True)
        editor._apply_char_format(italic)
        html = editor.get_edited_html()
        state = extract_lettersmith_style_state(html)
        self.assertIsNotNone(state)
        self.assertEqual(state["definitions"], editor.named_styles.active_set.to_dict())
        self.assertNotIn("editor_style_set_1", html)
        reopened = self._editor(html)
        self.assertEqual(
            reopened.named_styles.active_set, editor.named_styles.active_set
        )
        self._select(reopened, 1)
        self.assertEqual(reopened.named_styles.selected_style(), "heading_1")
        self.assertTrue(self._format_at(reopened, 0).fontItalic())
        changed = reopened.named_styles.active_set.update_style(
            "heading_1",
            replace(
                reopened.named_styles.active_set.get("heading_1"),
                font_family="Arial",
            ),
        )
        reopened.named_styles.load_set(changed)
        self.assertEqual(self._family_at(reopened, 0), "Arial")
        self.assertTrue(self._format_at(reopened, 0).fontItalic())
        self.assertIsNone(reopened._style_slots.load(1))

    def test_enter_after_title_creates_normal_paragraph_and_undoes_once(self) -> None:
        editor = self._editor("<p>Title</p>")
        self._select(editor, 1)
        editor._apply_named_style("title")
        self._select(editor, len(editor.editor.toPlainText()))
        event = QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress, QtCore.Qt.Key_Return, QtCore.Qt.NoModifier,
            "\r",
        )
        editor.editor.keyPressEvent(event)
        first = editor.editor.document().firstBlock()
        second = first.next()
        self.assertTrue(second.isValid())
        self._select(editor, 1)
        self.assertEqual(editor.named_styles.selected_style(), "title")
        self._select(editor, second.position())
        self.assertEqual(editor.named_styles.selected_style(), "normal_text")
        editor.editor.undo()
        self.assertEqual(editor.editor.toPlainText(), "Title")
        self._select(editor, 1)
        self.assertEqual(editor.named_styles.selected_style(), "title")

    def test_apply_to_empty_paragraph_sets_format_for_later_typing(self) -> None:
        editor = self._editor("<p>Other</p>")
        cursor = editor.editor.textCursor()
        cursor.setPosition(0)
        cursor.insertBlock()
        editor.editor.setTextCursor(cursor)
        self._select(editor, 0)
        editor._apply_named_style("title")
        second = editor.editor.document().firstBlock().next()
        self._select(editor, second.position())
        self._select(editor, 0)
        editor.editor.insertPlainText("New")
        self.assertEqual(editor.named_styles.selected_style(), "title")
        self.assertEqual(round(self._format_at(editor, 0).fontPointSize()), 28)

    def test_update_and_load_each_undo_definitions_and_linked_formatting(self) -> None:
        editor = self._editor("<p>One</p><p>Two</p>")
        self._select(editor, 0, len(editor.editor.toPlainText()))
        editor._apply_named_style("heading_1")
        baseline = editor.named_styles.active_set
        second_start = editor.editor.document().firstBlock().next().position()

        self._select(editor, 0, 3)
        editor.set_font_family(QtGui.QFont("Arial"))
        editor._update_named_style("heading_1")
        self.assertEqual(
            editor.named_styles.active_set.get("heading_1").font_family, "Arial"
        )
        self.assertEqual(self._family_at(editor, second_start), "Arial")
        editor.editor.undo()
        self.assertEqual(editor.named_styles.active_set, baseline)
        self.assertEqual(
            self._family_at(editor, second_start),
            baseline.get("heading_1").font_family,
        )

        replacement = baseline.update_style(
            "heading_1", replace(baseline.get("heading_1"), font_family="Georgia")
        )
        editor.named_styles.load_set(replacement)
        self.assertEqual(editor.named_styles.active_set, replacement)
        self.assertEqual(self._family_at(editor, second_start), "Georgia")
        editor.editor.undo()
        self.assertEqual(editor.named_styles.active_set, baseline)
        self.assertEqual(
            self._family_at(editor, second_start),
            baseline.get("heading_1").font_family,
        )

    def test_repeated_html_reopen_has_one_state_comment_and_no_root_table(self) -> None:
        editor = self._editor("<p>Alpha</p>")
        self._select(editor, 1)
        editor._apply_named_style("subtitle")
        html = editor.get_edited_html()
        for _ in range(3):
            self.assertEqual(html.count("lettersmith-style-state:v1:"), 1)
            self.assertNotIn("-qt-table-type: root", html)
            self.assertIsNotNone(extract_lettersmith_style_state(html))
            html = self._editor(html).get_edited_html()

    def test_rich_paste_inside_linked_paragraph_preserves_assignment(self) -> None:
        editor = self._editor("<p>Start</p>")
        self._select(editor, 1)
        editor._apply_named_style("heading_2")
        self._select(editor, len(editor.editor.toPlainText()))
        content = QtCore.QMimeData()
        content.setHtml('<span style="font-weight:700">Pasted</span>')
        editor.editor.insertFromMimeData(content)
        self.assertEqual(editor.editor.toPlainText(), "StartPasted")
        self._select(editor, len(editor.editor.toPlainText()) - 2)
        self.assertEqual(editor.named_styles.selected_style(), "heading_2")


if __name__ == "__main__":
    unittest.main()
