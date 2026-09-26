from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from Editor import Editor
from message_html import embed_lettersmith_style_state, extract_lettersmith_style_state
from named_text_styles import (
    DOCUMENT_STYLE_SCHEMA_VERSION, STYLE_KEYS, STYLE_LABELS, NamedStyleSet,
    ParagraphStyleDefinition, StyleDefinition, default_style_set,
)
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
            [f"Change Style to\nStyle {i}" for i in (1, 2, 3)],
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
        self.assertEqual(subtitle_label.font().pointSizeF(), normal_label.font().pointSizeF())
        self.assertEqual(current.get("subtitle").font_size, 23)
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

    def test_document_style_sizes_do_not_resize_toolbar_or_menu(self) -> None:
        load_application_fonts(Path(__file__).resolve().parents[1])
        editor = self._editor()
        editor.resize(1100, 720)
        editor.show()
        self.app.processEvents()
        dimensions = {}
        menu_size = None
        for document_size in (1, 40, 100):
            current = editor.named_styles.active_set
            for key in STYLE_KEYS:
                current = current.update_style(key, replace(current.get(key), font_size=document_size))
            editor.named_styles.load_set(current)
            expected = current.to_dict()
            menu = editor.named_style_menu
            menu.popup(editor.btn_named_style.mapToGlobal(QtCore.QPoint(0, editor.btn_named_style.height())))
            self.app.processEvents()
            if menu_size is None:
                menu_size = menu.size()
            self.assertEqual(menu.size(), menu_size)
            menu.hide()
            for key, action in zip(STYLE_KEYS, menu.actions()[:6]):
                with self.subTest(style=key, document_size=document_size):
                    self._select(editor, 1)
                    action.menu().actions()[0].trigger()
                    self.app.processEvents()
                    self.app.processEvents()  # Settle the toolbar's deferred layout request.
                    button = editor.btn_named_style
                    label = action._named_text_preview_row.label
                    geometry = (button.size(), editor.font_controls_scroll.height(), label.sizeHint())
                    dimensions.setdefault(key, geometry)
                    self.assertEqual(geometry, dimensions[key])
                    self.assertEqual(button.font().pointSizeF(), editor._named_style_selector_base_font.pointSizeF())
                    self.assertEqual(self._format_at(editor, 1).fontPointSize(), document_size)
                    self.assertEqual(editor.named_styles.active_set.to_dict(), expected)

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

    def test_update_title_copies_full_format_and_survives_undo_save_and_reopen(self) -> None:
        editor = self._editor("<p>Sample</p><p>Linked</p><p>Other</p>")
        first = editor.editor.document().firstBlock()
        linked = first.next()
        other = linked.next()
        self._select(editor, 0, other.position())
        editor._apply_named_style("title")
        paragraph = ParagraphStyleDefinition(
            alignment=int(QtCore.Qt.AlignRight), direction=0,
            line_height=150, line_height_type=1,
            top_margin=11, bottom_margin=13, left_margin=18, right_margin=12,
            text_indent=9, indent=2, list_style=-4, list_indent=3,
            list_start=7, list_prefix="(", list_suffix=")",
        )
        for family in ("Papyrus", "Arial"):
            with self.subTest(family=family):
                self._select(editor, 0, len(first.text()))
                editor.set_font_family(QtGui.QFont(family))
                editor.font_size_spin.setValue(40)
                direct = QtGui.QTextCharFormat()
                direct.setFontWeight(700)
                direct.setFontItalic(True)
                direct.setFontUnderline(True)
                direct.setFontStrikeOut(True)
                direct.setForeground(QtGui.QColor("#175fa4"))
                editor._apply_char_format(direct)
                editor.alignment_actions["right"].trigger()
                editor.spacing_actions[1.5].trigger()
                cursor = QtGui.QTextCursor(first)
                list_format = QtGui.QTextListFormat()
                list_format.setStyle(QtGui.QTextListFormat.ListDecimal)
                list_format.setIndent(3)
                list_format.setStart(7)
                list_format.setNumberPrefix("(")
                list_format.setNumberSuffix(")")
                if first.textList() is None:
                    cursor.createList(list_format)
                else:
                    first.textList().setFormat(list_format)
                fmt = cursor.blockFormat()
                fmt.setLayoutDirection(QtCore.Qt.LeftToRight)
                fmt.setTopMargin(11)
                fmt.setBottomMargin(13)
                fmt.setLeftMargin(18)
                fmt.setRightMargin(12)
                fmt.setTextIndent(9)
                fmt.setIndent(2)
                cursor.setBlockFormat(fmt)
                before = editor.named_styles.active_set
                linked_before = linked.blockFormat().properties()
                linked_font = self._format_at(editor, linked.position()).properties()
                expected = StyleDefinition(family, 40, "#175fa4", 700, True, True, True, paragraph)

                editor._refresh_named_style_menu()
                action = editor._style_update_actions["title"]
                self.assertTrue(action.isEnabled())
                action.trigger()
                self.assertEqual(editor.named_styles.active_set.get("title"), expected)
                self.assertEqual(linked.blockFormat().properties(), first.blockFormat().properties())
                self.assertEqual(linked.textList(), first.textList())
                self.assertEqual(first.textList().count(), 2)
                self.assertIsNone(other.textList())
                actual = self._format_at(editor, linked.position())
                self.assertEqual(actual.fontFamilies(), [family])
                self.assertEqual(actual.fontPointSize(), 40)
                self.assertEqual(actual.foreground().color().name(), "#175fa4")
                self.assertEqual(actual.fontWeight(), 700)
                self.assertTrue(actual.fontItalic())
                self.assertTrue(actual.fontUnderline())
                self.assertTrue(actual.fontStrikeOut())

                editor.editor.undo()
                self.assertEqual(editor.named_styles.active_set, before)
                self.assertEqual(linked.blockFormat().properties(), linked_before)
                self.assertEqual(self._format_at(editor, linked.position()).properties(), linked_font)
                editor.editor.redo()
                self.assertEqual(editor.named_styles.active_set.get("title"), expected)
                self.assertEqual(linked.blockFormat().properties(), first.blockFormat().properties())

        editor._save_style_slot(1)
        self.assertEqual(editor._style_slots.load(1), editor.named_styles.active_set)
        reopened = self._editor(editor.get_edited_html())
        self.assertEqual(reopened.named_styles.active_set, editor.named_styles.active_set)
        restored = reopened.editor.document().firstBlock()
        self.assertTrue(restored.blockFormat().alignment() & QtCore.Qt.AlignRight)
        self.assertEqual(restored.blockFormat().lineHeight(), 150)
        self.assertEqual(restored.blockFormat().topMargin(), 11)
        self.assertEqual(restored.blockFormat().bottomMargin(), 13)
        self.assertEqual(restored.blockFormat().leftMargin(), 18)
        self.assertEqual(restored.blockFormat().rightMargin(), 12)
        self.assertEqual(restored.blockFormat().textIndent(), 9)
        self.assertEqual(restored.blockFormat().indent(), 2)
        self.assertEqual(restored.textList().format().indent(), 3)
        self.assertEqual(restored.textList().itemText(restored), "(7)")
        self.assertEqual(restored.textList().itemText(restored.next()), "(8)")
        target = reopened.editor.document().lastBlock()
        self._select(reopened, target.position())
        reopened._apply_named_style("title")
        self.assertEqual(target.blockFormat().alignment(), QtCore.Qt.AlignRight)
        self.assertEqual(target.blockFormat().lineHeight(), 150)
        self.assertEqual(target.blockFormat().topMargin(), 11)
        self.assertEqual(target.blockFormat().textIndent(), 9)
        self.assertEqual(target.blockFormat().indent(), 2)
        self.assertEqual(target.textList().format().style(), QtGui.QTextListFormat.ListDecimal)
        self.assertEqual(target.textList().format().start(), 7)
        self.assertEqual(target.textList().format().numberPrefix(), "(")
        self.assertEqual(target.textList().format().numberSuffix(), ")")
        self.assertEqual(self._family_at(reopened, target.position()), "Arial")

    def test_normal_propagation_preserves_each_complete_style_and_linked_paragraph(self) -> None:
        editor = self._editor("".join(f"<p>{key}</p>" for key in STYLE_KEYS))
        sizes = {"normal_text": 12, "title": 40, "subtitle": 20,
                 "heading_1": 26, "heading_2": 22, "heading_3": 18}
        baseline = NamedStyleSet({
            key: replace(
                editor.named_styles.active_set.get(key),
                font_family="Arial", font_size=sizes[key],
                font_color=f"#{index + 1}23456", underline=bool(index % 2),
                paragraph=ParagraphStyleDefinition(
                    alignment=1, line_height=100 + index * 10, line_height_type=1,
                    top_margin=index * 2, bottom_margin=index * 3,
                    text_indent=index, list_style=-1 if key == "subtitle" else None,
                ),
            ) for index, key in enumerate(STYLE_KEYS)
        })
        editor.named_styles.load_set(baseline)
        block = editor.editor.document().firstBlock()
        block_formats = {}
        for key in STYLE_KEYS:
            self._select(editor, block.position())
            editor._apply_named_style(key)
            block_formats[key] = block.blockFormat().properties()
            block = block.next()
        first = editor.editor.document().firstBlock()
        self._select(editor, 0, len(first.text()))
        editor.set_font_family(QtGui.QFont("Papyrus"))
        editor.font_size_spin.setValue(13)
        editor.alignment_actions["right"].trigger()
        editor.spacing_actions[1.5].trigger()
        editor._refresh_named_style_menu()
        editor._style_update_actions["normal_text"].trigger()

        after = editor.named_styles.active_set
        self.assertEqual(after.get("normal_text"), replace(
            baseline.get("normal_text"), font_family="Papyrus", font_size=13,
            paragraph=replace(baseline.get("normal_text").paragraph, alignment=2, line_height=150),
        ))
        block = first.next()
        for key in STYLE_KEYS[1:]:
            with self.subTest(style=key):
                self.assertEqual(after.get(key), replace(baseline.get(key), font_family="Papyrus"))
                self.assertEqual(block.blockFormat().properties(), block_formats[key])
                self.assertEqual(self._family_at(editor, block.position()), "Papyrus")
                self.assertEqual(self._format_at(editor, block.position()).fontPointSize(), sizes[key])
                block = block.next()
        editor.editor.undo()
        self.assertEqual(editor.named_styles.active_set, baseline)

    def test_update_uses_first_selected_paragraph_format(self) -> None:
        editor = self._editor("<p>One</p><p>Two</p>")
        self._select(editor, 0, len(editor.editor.toPlainText()))
        editor._apply_named_style("normal_text")
        self._select(editor, 1)
        editor.alignment_actions["right"].trigger()
        self._select(editor, 0, len(editor.editor.toPlainText()))
        editor._refresh_named_style_menu()
        self.assertTrue(editor._style_update_actions["title"].isEnabled())
        editor._update_named_style("title")
        self.assertEqual(editor.named_styles.active_set.get("title").paragraph.alignment, 2)

    def test_mixed_selection_updates_from_first_selected_character(self) -> None:
        editor = self._editor("<p>Alpha Beta</p>")
        self._select(editor, 1)
        editor._apply_named_style("normal_text")
        self._select(editor, 0, 5)
        editor.set_font_family(QtGui.QFont("Arial"))
        self._select(editor, 0, len(editor.editor.toPlainText()))
        editor._refresh_named_style_menu()
        self.assertTrue(all(
            action.isEnabled() for action in editor._style_update_actions.values()
        ))
        self.assertIn(
            "first selected character",
            editor._style_update_actions["normal_text"].toolTip(),
        )
        self._select(editor, 0, 5)
        editor._refresh_named_style_menu()
        self.assertTrue(all(
            action.isEnabled() for action in editor._style_update_actions.values()
        ))
        editor._update_named_style("title")
        self.assertEqual(editor.named_styles.active_set.get("title").font_family, "Arial")

    def test_mouse_style_label_applies_and_submenu_updates_linked_heading(self) -> None:
        editor = self._editor('<p>Dear <a href="https://example.com">Reader</a></p>')
        editor.show()
        self._select(editor, 0, len(editor.editor.toPlainText()))
        menu = editor.named_style_menu
        title_action = menu.actions()[STYLE_KEYS.index("title")]
        menu.popup(editor.btn_named_style.mapToGlobal(QtCore.QPoint(0, 34)))
        self.app.processEvents()
        rect = menu.actionGeometry(title_action)
        QtTest.QTest.mouseClick(menu, QtCore.Qt.LeftButton, pos=QtCore.QPoint(rect.left() + 16, rect.center().y()))
        self.assertEqual(editor.named_styles.selected_style(), "title")
        self.assertFalse(menu.isVisible())
        for family in ("Papyrus", "Arial"):
            self._select(editor, 0, len(editor.editor.toPlainText()))
            editor.set_font_family(QtGui.QFont(family))
            self._select(editor, 5, 11)
            editor.toggle_italic()
            self._select(editor, 0, len(editor.editor.toPlainText()))
            menu.popup(editor.btn_named_style.mapToGlobal(QtCore.QPoint(0, 34)))
            menu.setActiveAction(title_action)
            QtTest.QTest.keyClick(menu, QtCore.Qt.Key_Right)
            self.app.processEvents()
            submenu = title_action.menu()
            self.assertTrue(submenu.isVisible())
            QtTest.QTest.mouseClick(submenu, QtCore.Qt.LeftButton,
                                   pos=submenu.actionGeometry(editor._style_update_actions["title"]).center())
            self.assertEqual(editor.named_styles.active_set.get("title").font_family, family)
        reopened = self._editor(editor.get_edited_html())
        self.assertEqual(reopened.named_styles.active_set.get("title").font_family, "Arial")

    def test_reopened_insertion_format_matches_visible_heading(self) -> None:
        editor = self._editor('<p style="font-family:Arial;font-size:40pt;">Heading</p>')
        self.assertEqual(editor.editor.currentCharFormat().fontFamilies(), ["Arial"])
        self.assertEqual(editor.editor.currentCharFormat().fontPointSize(), 40)
        editor._update_named_style("title")
        self.assertEqual(editor.named_styles.active_set.get("title").font_family, "Arial")

    def test_autosave_waits_for_significant_net_changes_and_manual_save_is_always_allowed(self) -> None:
        editor = self._editor('<p>Kassi</p>')
        with (mock.patch.object(type(editor.project_state), 'is_project_ready', new_callable=mock.PropertyMock, return_value=True),
              mock.patch.object(editor.project_save_service, 'save_message') as save,
              mock.patch.object(editor, '_sync_message_assets'),
              mock.patch.object(editor, '_apply_safe_language_corrections'),
              mock.patch('Editor.play_ui_sound')):
            original = editor._prepared_html()
            self._select(editor, 0, 5)
            editor.editor.insertPlainText('KASSI')
            self.assertEqual(editor._autosave_timer.interval(), 300000)
            editor._autosave_now()
            save.assert_not_called()
            editor.editor.undo()
            editor._autosave_now()
            save.assert_not_called()
            self.assertTrue(editor._save_document())
            self.assertTrue(editor.btn_save.isEnabled())
            self.assertTrue(editor._save_document())
            self.assertEqual(save.call_count, 2)
            self.assertEqual(save.call_args.args[0], original)
            save.reset_mock()
            editor.editor.moveCursor(QtGui.QTextCursor.End)
            editor.editor.insertPlainText(' ' + ' '.join(f'word{i}' for i in range(16)))
            editor._autosave_now()
            save.assert_called_once()
            self.assertEqual(save.call_args.kwargs['reason'], 'autosave')
            editor._autosave_now()
            self.assertEqual(save.call_count, 1)

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

    def test_legacy_letter_styles_upgrade_without_losing_formatting(self) -> None:
        editor = self._editor("<p>Legacy title</p>")
        editor._apply_named_style("title")
        state = editor.named_styles.serialize()
        state["schema_version"] = 1
        state["definitions"]["version"] = 1
        for definition in state["definitions"]["styles"].values():
            definition.pop("paragraph")
        legacy = embed_lettersmith_style_state(editor.get_edited_html(), state)
        reopened = self._editor(legacy)
        self.assertEqual(reopened.named_styles.active_set, editor.named_styles.active_set)
        self.assertEqual(reopened.named_styles.selected_style(), "title")
        self.assertEqual(self._format_at(reopened, 0).fontPointSize(), 28)
        self.assertEqual(
            extract_lettersmith_style_state(reopened.get_edited_html())["schema_version"],
            DOCUMENT_STYLE_SCHEMA_VERSION,
        )

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
            self.assertEqual(html.count(f"lettersmith-style-state:v{DOCUMENT_STYLE_SCHEMA_VERSION}:"), 1)
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
