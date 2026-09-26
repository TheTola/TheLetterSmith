from __future__ import annotations

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextListFormat
from PySide6.QtWidgets import QApplication, QTextEdit

from named_text_style_document import NamedTextStyleDocument
from named_text_styles import default_style_set


class NamedTextStyleDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _at(editor: QTextEdit, position: int) -> QTextCharFormat:
        cursor = QTextCursor(editor.document())
        cursor.setPosition(position)
        cursor.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
        return cursor.charFormat()

    def test_export_removes_only_root_table_and_preserves_inner_content(self) -> None:
        editor = QTextEdit()
        editor.setHtml(
            '<table><tr><td><p>Cell <a href="https://example.com">Link</a>'
            '</p></td></tr></table><p><img src="asset.png" /> End</p>'
        )
        styles = NamedTextStyleDocument(editor, default_style_set())
        self.assertEqual(editor.toHtml().count("<table"), 2)
        exported = styles.export_html()
        self.assertEqual(exported.count("<table"), 1)
        self.assertNotIn("-qt-table-type: root", exported)
        self.assertIn('href="https://example.com"', exported)
        self.assertIn('src="asset.png"', exported)

    def test_load_preserves_direct_override_even_if_baseline_temporarily_matches(self) -> None:
        editor = QTextEdit()
        editor.setPlainText("Direct")
        styles = NamedTextStyleDocument(editor, default_style_set())
        styles.apply_style("heading_1")
        cursor = editor.textCursor()
        cursor.select(QTextCursor.Document)
        editor.setTextCursor(cursor)
        direct = QTextCharFormat()
        direct.setFontFamilies(["Arial"])
        styles.apply_direct_format(direct)
        initial = styles.active_set
        matching = initial.update_style(
            "heading_1", replace(initial.get("heading_1"), font_family="Arial")
        )
        changed = initial.update_style(
            "heading_1", replace(initial.get("heading_1"), font_family="Georgia")
        )
        styles.load_set(matching)
        styles.load_set(changed)
        self.assertEqual(self._at(editor, 0).fontFamilies(), ["Arial"])
        self.assertEqual(
            styles.serialize()["blocks"][0]["overrides"][0]["mask"],
            ["font_family"],
        )

    def test_rich_paste_in_styled_paragraph_keeps_assignment_and_direct_font(self) -> None:
        editor = QTextEdit()
        editor.setPlainText("Start")
        styles = NamedTextStyleDocument(editor, default_style_set())
        styles.apply_style("heading_1")
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        editor.setTextCursor(cursor)
        content = QMimeData()
        content.setHtml('<span style="font-family:Arial">Pasted</span>')
        context = styles.before_paste(content)
        editor.insertFromMimeData(content)
        styles.after_paste(context)
        self.assertEqual(editor.toPlainText(), "StartPasted")
        self.assertEqual(styles.selected_style(), "heading_1")
        changed = styles.active_set.update_style(
            "heading_1",
            replace(styles.active_set.get("heading_1"), font_family="Georgia"),
        )
        styles.load_set(changed)
        self.assertEqual(self._at(editor, 0).fontFamilies(), ["Georgia"])
        self.assertEqual(self._at(editor, 5).fontFamilies(), ["Arial"])

    def test_update_promotes_redundant_direct_override_without_definition_change(self) -> None:
        editor = QTextEdit()
        editor.setPlainText("Same")
        styles = NamedTextStyleDocument(editor, default_style_set())
        styles.apply_style("normal_text")
        cursor = editor.textCursor()
        cursor.select(QTextCursor.Document)
        editor.setTextCursor(cursor)
        direct = QTextCharFormat()
        direct.setFontFamilies([styles.active_set.get("normal_text").font_family])
        styles.apply_direct_format(direct)
        self.assertEqual(
            styles.serialize()["blocks"][0]["overrides"][0]["mask"],
            ["font_family"],
        )
        styles.update_style("normal_text")
        self.assertEqual(styles.serialize()["blocks"][0]["overrides"], [])

    def test_learned_list_styles_share_numbering_and_detach_only_styled_blocks(self) -> None:
        editor = QTextEdit()
        editor.setPlainText("One\nTwo\nThree\nPlain")
        styles = NamedTextStyleDocument(editor, default_style_set())
        first = editor.document().firstBlock()
        second = first.next()
        third = second.next()
        plain = third.next()
        editor.setTextCursor(QTextCursor(plain))
        styles.update_style("normal_text")
        cursor = QTextCursor(first)
        cursor.setPosition(plain.position() - 1, QTextCursor.KeepAnchor)
        list_format = QTextListFormat()
        list_format.setStyle(QTextListFormat.ListDisc)
        cursor.createList(list_format)
        editor.setTextCursor(QTextCursor(first))
        styles.update_style("title")
        self.assertEqual(styles.active_set.get("title").paragraph.list_style, -1)

        # Applying the learned plain style removes one item, not its siblings.
        editor.setTextCursor(QTextCursor(second))
        styles.apply_style("normal_text")
        self.assertIsNone(second.textList())
        self.assertEqual(first.textList(), third.textList())
        self.assertEqual(first.textList().count(), 2)
        styles.apply_style("title")
        self.assertEqual(first.textList(), second.textList())
        self.assertEqual(first.textList().count(), 3)

        cursor = QTextCursor(plain)
        list_format.setStyle(QTextListFormat.ListDecimal)
        list_format.setStart(3)
        cursor.createList(list_format)
        editor.setTextCursor(cursor)
        styles.update_style("heading_1")
        cursor = QTextCursor(first)
        cursor.setPosition(third.position() - 1, QTextCursor.KeepAnchor)
        editor.setTextCursor(cursor)
        styles.apply_style("heading_1")
        self.assertEqual(first.textList(), second.textList())
        self.assertEqual(first.textList().itemText(first), "3.")
        self.assertEqual(second.textList().itemText(second), "4.")
        self.assertEqual(third.textList().format().style(), QTextListFormat.ListDisc)
        self.assertEqual(third.textList().count(), 1)
        editor.undo()
        self.assertEqual(first.textList(), third.textList())
        self.assertEqual(first.textList().count(), 3)
        list_format.setNumberSuffix("")
        plain.textList().setFormat(list_format)
        editor.setTextCursor(QTextCursor(plain))
        styles.update_style("heading_2")
        editor.setTextCursor(QTextCursor(first))
        styles.apply_style("heading_2")
        self.assertEqual(first.textList().itemText(first), "3")


if __name__ == "__main__":
    unittest.main()
