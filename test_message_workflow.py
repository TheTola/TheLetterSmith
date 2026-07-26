from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextBlockFormat, QTextCursor

from Editor import Editor
from Message_tab import MessageTab
from message_format import (
    DEFAULT_MESSAGE_FONT,
    DEFAULT_MESSAGE_LINE_SPACING,
    message_statistics,
    normalize_imported_message_html,
)
from message_history import MAX_MESSAGE_REVISIONS, MessageHistory
from message_html import count_message_html_words
from settings_store import SettingsStore
from Template import TEMPLATE_CSS


def _app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _EditorParent(QtWidgets.QWidget):
    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.project_root = str(project_root)


def _editor(tmp_path: Path, html: str) -> Editor:
    _app()
    parent = _EditorParent(tmp_path)
    editor = Editor(html, parent=parent)
    editor._test_parent = parent
    return editor


def test_blank_message_defaults_and_new_paragraphs_inherit(tmp_path: Path) -> None:
    editor = _editor(tmp_path, "<p></p>")
    cursor = editor.editor.textCursor()
    cursor.movePosition(QTextCursor.End)
    cursor.insertText("First")
    cursor.insertBlock()
    cursor.insertText("Second")
    editor.editor.setTextCursor(cursor)

    block = editor.editor.document().lastBlock()
    assert block.blockFormat().alignment() == Qt.AlignCenter
    assert block.blockFormat().lineHeight() == 200
    assert editor.editor.document().defaultFont().family() == DEFAULT_MESSAGE_FONT
    assert DEFAULT_MESSAGE_FONT in editor.get_edited_html()
    assert editor._export_line_spacing == DEFAULT_MESSAGE_LINE_SPACING
    assert editor.btn_spacing.text() == "Spacing: Double"
    assert editor.btn_align.text() == "Align: Center"
    editor._allow_close = True
    editor.close()


def test_existing_saved_format_and_manual_override_are_preserved(tmp_path: Path) -> None:
    html = '<p style="text-align:right;font-family:Arial;line-height:150%">Saved</p>'
    editor = _editor(tmp_path, html)

    first = editor.editor.document().firstBlock()
    assert first.blockFormat().alignment() == Qt.AlignRight
    assert "Arial" in editor.get_edited_html()
    assert editor._export_line_spacing is None

    cursor = editor.editor.textCursor()
    cursor.select(QTextCursor.Document)
    editor.editor.setTextCursor(cursor)
    editor._set_alignment(Qt.AlignLeft, "Left")
    editor.set_font_family(QtGui.QFont("Courier New"))
    saved = editor.get_edited_html()
    assert editor.editor.document().firstBlock().blockFormat().alignment() == Qt.AlignLeft
    assert "Courier New" in saved
    editor._allow_close = True
    editor.close()


def test_import_defaults_and_unbounded_message_import(tmp_path: Path) -> None:
    normalized = normalize_imported_message_html("<p>Imported <b>message</b></p>")
    assert DEFAULT_MESSAGE_FONT in normalized
    assert 'align="center"' in normalized
    assert "line-height:200%" in normalized

    _app()
    tab = MessageTab(str(tmp_path))
    source = tmp_path / "long.txt"
    source.write_text(" ".join(f"word{index}" for index in range(1205)), encoding="utf-8")
    tab._generate_image = lambda _html: None
    tab._emit_best_preview = lambda: None
    tab._ensure_wall_exists = lambda: True

    tab._process_file(str(source))

    stored = (tmp_path / "gallery/user/message/message.html").read_text(encoding="utf-8")
    assert count_message_html_words(stored) == 1205
    assert DEFAULT_MESSAGE_FONT in stored


def test_message_statistics_uses_visible_text_and_reading_time() -> None:
    stats = message_statistics(
        "<style>hidden words</style><p>One &amp; two<br>three four five</p>"
    )
    assert stats.words == 5
    assert stats.characters == len("One & two\nthree four five")
    assert stats.reading_minutes == 1

    long_stats = message_statistics("<p>" + "word " * 842 + "</p>")
    assert long_stats.words == 842
    assert long_stats.reading_minutes == 4


def test_overlay_controls_live_in_message_tab_not_editor(tmp_path: Path) -> None:
    _app()
    tab = MessageTab(str(tmp_path))
    assert set(tab.overlay_buttons) == {"black", "white", "paper", "clear"}
    assert tab.overlay_opacity_slider.maximum() == 100

    editor = _editor(tmp_path, "<p></p>")
    assert not hasattr(editor, "overlay_panel")
    editor._allow_close = True
    editor.close()

    tab._generate_image = lambda _html: None
    tab._emit_best_preview = lambda: None
    tab.current_html = "<p>Overlay preview</p>"
    previews: list[str] = []
    tab.text_selected.connect(previews.append)
    tab._set_overlay_preset("black")
    tab._overlay_preview_timer.stop()
    tab._refresh_overlay_previews()
    settings = SettingsStore(tmp_path).snapshot()
    assert settings["message_overlay_preset"] == "black"
    assert previews and "rgba(0,0,0" in previews[-1]


def test_existing_message_file_is_unchanged_on_tab_startup(tmp_path: Path) -> None:
    _app()
    message_path = tmp_path / "gallery/user/message/message.html"
    message_path.parent.mkdir(parents=True)
    existing = '<p style="text-align:right;font-family:Arial">Intentional</p>'
    message_path.write_text(existing, encoding="utf-8")

    MessageTab(str(tmp_path))

    assert message_path.read_text(encoding="utf-8") == existing
    assert "font-family:Papyrus,fantasy" in TEMPLATE_CSS
    assert "text-align:center" in TEMPLATE_CSS
    assert "line-height:2" in TEMPLATE_CSS


def test_editor_autosave_is_debounced_atomic_and_emits(tmp_path: Path) -> None:
    editor = _editor(tmp_path, "<p></p>")
    received: list[str] = []
    editor.autosaved.connect(received.append)

    cursor = editor.editor.textCursor()
    cursor.movePosition(QTextCursor.End)
    cursor.insertText("Autosaved content")
    editor.editor.setTextCursor(cursor)
    assert editor._autosave_timer.isSingleShot()
    assert editor._autosave_timer.interval() == 1500
    editor._autosave_timer.stop()
    editor._autosave_message()

    message_path = tmp_path / "gallery/user/message/message.html"
    assert "Autosaved content" in message_path.read_text(encoding="utf-8")
    assert received and "Autosaved content" in received[-1]
    assert not list(message_path.parent.glob("*.tmp*"))
    editor._allow_close = True
    editor.close()


def test_manual_save_creates_a_revision(tmp_path: Path) -> None:
    editor = _editor(tmp_path, "<p></p>")
    cursor = editor.editor.textCursor()
    cursor.insertText("Manual save")
    editor.editor.setTextCursor(cursor)

    editor.apply_changes()

    revisions = MessageHistory(tmp_path).list_revisions()
    assert revisions
    assert any("Manual save" in revision.preview for revision in revisions)


def test_revision_limit_and_restore_snapshot_current_message(tmp_path: Path) -> None:
    message_path = tmp_path / "gallery/user/message/message.html"
    message_path.parent.mkdir(parents=True)
    message_path.write_text("<p>Current</p>", encoding="utf-8")
    history = MessageHistory(tmp_path)

    for index in range(MAX_MESSAGE_REVISIONS + 5):
        history.create_revision(f"<p>Revision {index}</p>", force=True)

    revisions = history.list_revisions()
    assert len(revisions) == MAX_MESSAGE_REVISIONS
    selected = revisions[-1]
    selected_html = selected.path.read_text(encoding="utf-8")

    history.restore(selected.path)

    assert message_path.read_text(encoding="utf-8") == selected_html
    assert any("Current" in revision.preview for revision in history.list_revisions())
